"""
Quick testing script to dispatch tasks to the Celery worker and listen to Redis Pub/Sub events.

Usage:
    uv run python test_dispatch.py [buyer_risk|credit_limit|sync]
"""

import sys
import os
import json
import time
import threading

# Add src directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from celery import Celery
import redis

REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://:default@localhost:6379/0")


def listen_pubsub():
    """Background listener to print Redis Pub/Sub events live."""
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        pubsub.psubscribe("rysknode-events:*")
        print("\n📡 Listening for Redis Pub/Sub response on channel 'rysknode-events:*'...\n")

        for message in pubsub.listen():
            if message["type"] == "pmessage":
                channel = message["channel"]
                payload = json.loads(message["data"])
                print(f"\n🎉 [EVENT RECEIVED] Channel: {channel}")
                print(f"   Task ID: {payload.get('task_id')}")
                print(f"   Status:  {payload.get('status')}")
                print(f"   Result Summary:")
                result = payload.get("result", {})
                if isinstance(result, dict):
                    for k in ("company_name", "pralyon_score", "blended_pd", "final_band", "evaluated_limit"):
                        if k in result:
                            print(f"     • {k}: {result[k]}")
                print("=" * 60)
                break
    except Exception as e:
        print(f"PubSub listener error: {e}")


def dispatch_task(task_type: str = "buyer_risk"):
    client = Celery(broker=REDIS_URL)

    # Start PubSub listener thread
    listener_thread = threading.Thread(target=listen_pubsub, daemon=True)
    listener_thread.start()
    time.sleep(0.5)

    if task_type == "buyer_risk":
        print("🚀 Dispatching Buyer Risk Assessment (Queue: buyer_risk)...")
        res = client.send_task(
            "asyncworker.tasks.assess_buyer_task",
            kwargs={
                "entity_id": "U63010MH1959PTC011380",
                "seller_id": "SELLER_001",
                "trade_name": "Umargo",
                "state_code": "MH",
                "include_xai": False,
            },
            queue="buyer_risk",
        )
        print(f"✓ Task sent! Task ID: {res.id}")

    elif task_type == "credit_limit":
        print("🚀 Dispatching Credit Limit Assessment (Queue: credit_limit)...")
        res = client.send_task(
            "asyncworker.tasks.assess_credit_limit_task",
            kwargs={
                "entity_id": "U63010MH1959PTC011380",
                "seller_id": "SELLER_001",
                "requested_amount": 500000.0,
                "credit_period_days": 30,
            },
            queue="credit_limit",
        )
        print(f"✓ Task sent! Task ID: {res.id}")

    elif task_type == "sync":
        print("🚀 Dispatching MCA / Data.gov.in Sync (Queue: sync)...")
        res = client.send_task(
            "asyncworker.tasks.sync_data_gov_state",
            kwargs={"statecode": "MP"},
            queue="sync",
        )
        print(f"✓ Task sent! Task ID: {res.id}")

    # Wait a few seconds for completion
    time.sleep(6)


if __name__ == "__main__":
    choice = sys.argv[1] if len(sys.argv) > 1 else "buyer_risk"
    dispatch_task(choice)
