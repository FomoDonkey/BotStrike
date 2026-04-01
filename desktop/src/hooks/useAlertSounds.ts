import { useEffect, useRef } from "react";
import { useAlertStore } from "@/stores/alertStore";
import { sounds } from "@/lib/sounds";

export function useAlertSounds() {
  const alertCount = useAlertStore((s) => s.alerts.length);
  const soundEnabled = useAlertStore((s) => s.soundEnabled);
  const lastCount = useRef(0);

  useEffect(() => {
    if (!soundEnabled || alertCount <= lastCount.current) {
      lastCount.current = alertCount;
      return;
    }
    lastCount.current = alertCount;

    // Play sound for the latest alert
    const alerts = useAlertStore.getState().alerts;
    const latest = alerts[alerts.length - 1];
    if (!latest || latest.dismissed) return;

    const soundKey = latest.sound;
    if (soundKey && soundKey in sounds) {
      sounds[soundKey as keyof typeof sounds]();
    } else if (latest.level === "critical") {
      sounds.alert();
    }
  }, [alertCount, soundEnabled]);
}
