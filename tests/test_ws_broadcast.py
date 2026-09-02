"""ChannelManager.broadcast must survive a client disconnecting while another send awaits
(CT 2026-09-02 04:28Z: 348x 'Set changed size during iteration', whole tick dropped)."""
import asyncio

import server.bridge as bridge


class _WS:
    def __init__(self, mgr, channel, victim=None, fail=False):
        self.mgr, self.channel, self.victim, self.fail = mgr, channel, victim, fail
        self.received = []

    async def send_text(self, msg):
        await asyncio.sleep(0)                      # yield like a real socket write
        if self.victim is not None:
            self.mgr.disconnect(self.channel, self.victim)   # concurrent disconnect
        if self.fail:
            raise RuntimeError("closed")
        self.received.append(msg)


def test_broadcast_survives_concurrent_disconnect_and_prunes_dead_clients():
    mgr = bridge.ChannelManager()
    clients = mgr._channels["market"]
    a, b, c = _WS(mgr, "market"), _WS(mgr, "market"), _WS(mgr, "market", fail=True)
    a.victim = b                                    # a's send removes b mid-iteration
    d = _WS(mgr, "market")
    for ws in (a, b, c, d):
        clients.add(ws)
    asyncio.run(mgr.broadcast("market", {"x": 1}))  # must not raise
    assert a.received == ['{"x": 1}'] and d.received == ['{"x": 1}']
    assert c not in clients and b not in clients and a in clients and d in clients
    assert mgr.client_count == 2
