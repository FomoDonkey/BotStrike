try:
    from .collector import StrikeDataCollector
except ImportError:
    pass  # collector archived — only binance_downloader available
