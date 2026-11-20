"""Try the phone agent by typing instead of talking: no Twilio, no phone call.

    .venv/bin/python -m app.calls.simulate --name "Asha Rao" --course ai-agents-devops

Needs MongoDB and Claude credentials. Works in a separate "<MONGODB_DB>_simulation" database,
so nothing lands in your real `feedbacks` collection.
"""
import argparse
import asyncio
import os

from .. import config, db
from ..deps import load_courses
from . import session


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Asha Rao")
    parser.add_argument("--course", default=load_courses()[0].id)
    parser.add_argument("--keep", action="store_true", help="resume the previous simulated call's answers")
    args = parser.parse_args()
    titles = {c.id: c.title for c in load_courses()}
    if args.course not in titles:
        parser.error(f"unknown course; choose from {', '.join(titles)}")

    os.environ["MONGODB_DB"] = config.mongodb_db() + "_simulation"
    database = await db.connect()
    key = {"email": "simulated@example.com", "course_id": args.course}
    if not args.keep:
        await database.call_contacts.delete_one(key)
        await database.feedbacks.delete_one(key)
    await database.call_contacts.update_one(
        key,
        {
            "$set": {"name": args.name, "phone": "+10000000000", "status": "calling", "updated_at": db.now()},
            "$setOnInsert": {"answers": {}, "transcript": [], "attempts": 0, "created_at": db.now()},
        },
        upsert=True,
    )
    contact = await database.call_contacts.find_one(key)

    async def send(message: dict) -> None:
        print(message["token"], end="\n" if message["last"] else "", flush=True)

    call = session.CallSession(database, contact, titles[args.course], send)
    await call.started(None)
    print(f"(type what the person says; Ctrl-D hangs up)\n\nAgent: {call.opening}")
    try:
        while call.outcome is None:
            heard = (await asyncio.to_thread(input, "You: ")).strip()
            if heard:
                print("Agent: ", end="")
                await call.handle_prompt(heard)
    except EOFError:
        print()
    await call.finish()

    contact = await database.call_contacts.find_one(key)
    print(f"\nstatus: {contact['status']}  outcome: {contact.get('outcome')}\nanswers: {contact['answers']}")
    feedback = await database.feedbacks.find_one(key, {"_id": 0})
    print(f"submitted feedback: {feedback}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
