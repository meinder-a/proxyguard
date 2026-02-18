import asyncio
import sys
import os

# fix path so we can run from root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from proxy_guard.server import start

if __name__ == "__main__":
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        pass
