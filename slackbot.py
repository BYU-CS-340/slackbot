# Copyright 2025 Brigham Young University. All rights reserved.

from pathlib import Path
import re
import sys
import json
import os
from typing import Any, Callable

import sqlite3


TA_DICT_PATH = Path("./secrets/ta_slack_member_ids.json")


# Util functions


def build_queue_string(users: list[Any]) -> str:
    return "\n".join([f"{i}) <@{user}>" for i, user in enumerate(users)])


# Database, and folder it is in, must be writeable. This is why it is in a ./db folder.
# There is no error handling here - Use self.lastErrorMsg() to get error messages when things break.


class PersistentQueue:
    QUEUE_TYPES = ("online", "inperson")

    def __init__(self) -> None:
        db_name = "./db/queue.sqlite"
        Path(db_name).parent.mkdir(parents=True, exist_ok=True)
        db_exists = os.path.exists(db_name)
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        if not db_exists:
            os.chmod(db_name, 0o777)
        self.create_tables()

    def __del__(self) -> None:
        if hasattr(self, "conn"):
            self.conn.close()

    def create_tables(self) -> None:
        # Serialize migration with joins/dequeues from other HTTP requests.
        with self.conn:
            self.cursor.execute("BEGIN IMMEDIATE")
            self.cursor.execute("CREATE TABLE IF NOT EXISTS queue(user TEXT NOT NULL);")
            columns = {row[1] for row in self.cursor.execute("PRAGMA table_info(queue)")}
            if "queue_type" not in columns:
                # Preserve existing students and their original rowid/join order.
                self.cursor.execute(
                    "ALTER TABLE queue ADD COLUMN queue_type TEXT NOT NULL "
                    "DEFAULT 'inperson' CHECK(queue_type IN ('online', 'inperson'))"
                )

    def get_entries(self, queue_type: str | None = None) -> list[tuple[str, str]]:
        if queue_type is None:
            self.cursor.execute("SELECT user, queue_type FROM queue ORDER BY rowid")
        else:
            self.cursor.execute(
                "SELECT user, queue_type FROM queue WHERE queue_type = ? ORDER BY rowid",
                [queue_type],
            )
        return self.cursor.fetchall()

    def get_users_in_queue(self, queue_type: str | None = None) -> list[str]:
        return [user for user, _ in self.get_entries(queue_type)]

    def get_postion_in_queue(self, user_id: str, queue_type: str | None = None) -> int | None:
        users = self.get_users_in_queue(queue_type)
        return users.index(user_id) if user_id in users else None

    def get_num_users_in_queue(self, queue_type: str | None = None) -> int:
        return len(self.get_users_in_queue(queue_type))

    def is_user_in_queue(self, user_id: str) -> bool:
        return user_id in self.get_users_in_queue()

    def add_user_to_queue(self, user_id: str, queue_type: str = "inperson") -> int | None:
        if queue_type not in self.QUEUE_TYPES:
            raise ValueError(f"Unknown queue: {queue_type}")
        with self.conn:
            self.cursor.execute("BEGIN IMMEDIATE")
            if self.is_user_in_queue(user_id):
                return None
            self.cursor.execute(
                "INSERT INTO queue (user, queue_type) VALUES (?, ?)", [user_id, queue_type]
            )
            return self.get_postion_in_queue(user_id, queue_type)

    def remove_user_from_queue(self, user_id: str) -> None:
        with self.conn:
            self.cursor.execute("DELETE FROM queue WHERE user = ?", [user_id])

    def clear_queue(self) -> None:
        with self.conn:
            self.cursor.execute("DELETE FROM queue")

    def next_entry(self, queue_type: str | None = None) -> tuple[str, str] | None:
        # rowid orders all joins, including ties in wall-clock time. Taking the
        # oldest entry and removing it must be atomic across concurrent TAs.
        with self.conn:
            self.cursor.execute("BEGIN IMMEDIATE")
            entries = self.get_entries(queue_type)
            if not entries:
                return None
            first = entries[0]
            self.cursor.execute("DELETE FROM queue WHERE user = ?", [first[0]])
            return first

    def next(self, queue_type: str | None = None) -> str | None:
        entry = self.next_entry(queue_type)
        return entry[0] if entry else None

    def is_user_a_ta(self, user_id: str) -> bool:
        with TA_DICT_PATH.open("r") as f:
            ta_user_ids = json.load(f)
        return user_id in ta_user_ids


class Request:
    def __init__(self, action: str, args: list[str], requester_id: str) -> None:
        self.action: str = action
        self.args: list[str] = args
        self.requester_id: str = requester_id


class Response:
    def __init__(self, text: str) -> None:
        self.text: str = text


class ChannelResponse(Response):
    response_type = "in_channel"

    def __init__(self, text: str) -> None:
        self.text: str = text


class PrivateResponse(Response):
    def __init__(self, text: str) -> None:
        super().__init__(text)


# Action handlers


def build_queues_string(entries: list[tuple[str, str]]) -> str:
    sections = []
    for queue_type, label in (("online", "Online"), ("inperson", "In-person")):
        users = [user for user, kind in entries if kind == queue_type]
        sections.append(f"{label} queue ({len(users)}):\n" + (build_queue_string(users) or "Empty."))
    return "\n\n".join(sections)


def handle_wait(req: Request) -> None:
    p = PersistentQueue()
    entries = p.get_entries()
    online = sum(kind == "online" for _, kind in entries)
    msg = f"There are {len(entries)} people across both queues ({online} online, {len(entries) - online} in-person)!"
    for position, (user, kind) in enumerate(entries):
        if user == req.requester_id:
            local_position = sum(other_kind == kind for _, other_kind in entries[:position])
            label = "in-person" if kind == "inperson" else kind
            msg += f" You are in the {label} queue, with {local_position} people ahead in that queue and {position} ahead across both queues."
            break
    send_message(msg)


def handle_passoff(req: Request) -> None:
    if req.action == "passoff":
        send_message("Please choose /passoff-online or /passoff-inperson to join a queue.")
        return
    queue_type = req.action.removeprefix("passoff-")
    p = PersistentQueue()
    position = p.add_user_to_queue(req.requester_id, queue_type)
    if position is None:
        send_message("You're already in a passoff queue! Use /nevermind before joining a different queue.")
        return
    label = "in-person" if queue_type == "inperson" else queue_type
    send_message(f"You were added to the {label} passoff queue. There are {position} people in front of you in that queue.", private=False)


def handle_nevermind(req: Request) -> None:
    if req.args:
        require_ta_or_halt(req, "Students can only remove themselves, by using /nevermind with no arguments.")
        user_id, _ = parse_arg_as_user(req.args[0])
        pronoun = "they"
    else:
        user_id = req.requester_id
        pronoun = "you"

    p = PersistentQueue()
    if not p.is_user_in_queue(user_id):
        send_message(f"Couldn't remove {pronoun} from the queue. Were {pronoun} in it?")
    else:
        p.remove_user_from_queue(user_id)
        send_message(f"Successfully removed {pronoun} from the queue.")


def handle_next(req: Request) -> None:
    p = PersistentQueue()
    queue_type = None if req.action == "next" else req.action.removeprefix("next-")
    entry = p.next_entry(queue_type)
    if entry is None:
        label = "either queue" if queue_type is None else ("the in-person queue" if queue_type == "inperson" else "the online queue")
        send_message(f"Hmm, no one is in {label}, so nothing was done.")
        return
    first, kind = entry
    instruction = f"Please DM <@{req.requester_id}> the link to your Zoom meeting." if kind == "online" else "Please come in."
    send_message(f"You are up <@{first}>. {instruction}", private=False)


def handle_queue(req: Request) -> None:
    entries = PersistentQueue().get_entries()
    send_message(f"There are {len(entries)} people across both queues.\n{build_queues_string(entries)}")


def handle_clear_queue(req: Request) -> None:
    p = PersistentQueue()
    entries = p.get_entries()
    if not entries:
        send_message("Ain't nobody here.")
        return
    p.clear_queue()
    send_message(f"The following {len(entries)} people were cleared from both queues:\n{build_queues_string(entries)}", private=False)


def handle_close_queue(req: Request) -> None:
    p = PersistentQueue()
    entries = p.get_entries()
    send_message(
        "Closing both queues for the night.\n\nSorry we couldn't get to everyone, "
        "but don't worry--if you were on either queue an hour before the official closing time "
        "(according to the schedule, not this message), you can still get credit tomorrow "
        "as if you had passed off today."
        f"\n\n{build_queues_string(entries)}"
        "\n\n'Night, y'all!",
        private=False,
    )
    p.clear_queue()


def handle_ta(req: Request) -> None:
    with TA_DICT_PATH.open("r") as f:
        ta_dict = json.load(f)

    if len(req.args) == 0:
        ta_list_string = "\n".join([f"<@{ta_dict[ta_id]}> ({ta_id})" for ta_id in ta_dict])
        send_message(f"Current TA list:\n{ta_list_string}")
        return
    if len(req.args) != 2:
        send_error("Invalid usage. This command needs 0 or 2 arguments.")
        return

    subcmd = req.args[0]
    specified_user_id, specified_user_name = parse_arg_as_user(req.args[1])

    if subcmd == "add":
        if specified_user_id in ta_dict:
            send_message("User is already a TA.")
            return
        ta_dict[specified_user_id] = specified_user_name

    elif subcmd == "rmv":
        if specified_user_id not in ta_dict:
            send_message("User is not currently a TA.")
            return
        del ta_dict[specified_user_id]

    else:
        send_error("Invalid usage. Valid subcommands are '/ta add <user>' and '/ta rmv <user>'")
        return

    with TA_DICT_PATH.open("w") as f:
        json.dump(ta_dict, f)
    send_message("Updated TA list successfully.")


def handle_bottest(req: Request) -> None:
    msg = f"action: '{req.action}'\nargs: {req.args}\nrequester_id: '{req.requester_id}'"
    send_message(msg)


def run_action(req: Request) -> None:
    student_handlers: dict[str, Callable[[Request], None]] = {
        "wait": handle_wait,
        "passoff": handle_passoff,
        "passoff-online": handle_passoff,
        "passoff-inperson": handle_passoff,
        "nevermind": handle_nevermind,
        "queue": handle_queue,
    }

    ta_only_handlers: dict[str, Callable[[Request], None]] = {
        "next": handle_next,
        "next-online": handle_next,
        "next-inperson": handle_next,
        "clearqueue": handle_clear_queue,
        "closequeue": handle_close_queue,
        "ta": handle_ta,
        "bottest": handle_bottest,
    }

    if req.action in student_handlers:
        handler = student_handlers[req.action]
        handler(req)
        return

    if req.action in ta_only_handlers:
        require_ta_or_halt(req)
        handler = ta_only_handlers[req.action]
        handler(req)
        return

    send_error(f"Unrecognized action: '{req.action}'. Args were '{req.args}'")


def require_ta_or_halt(req: Request, msg: str = "") -> None:
        p = PersistentQueue()
        if not p.is_user_a_ta(req.requester_id):
            send_message("TA command only, sorry. " + msg)
            sys.exit()


def send_message(msg: str, private: bool = True) -> None:
    res = {"text": msg} if private else {"text": msg, "response_type": "in_channel"}
    res_str = json.dumps(res)
    print(res_str)


def send_error(msg: str) -> None:
    send_message(f"Error (please contact a TA!): {msg}")
    sys.exit(1)


def parse_arg_as_user(raw_arg: str) -> tuple[str, str]:
    pattern = re.compile(r"<@(.*)\|(.*)>")
    match = pattern.search(raw_arg)
    if match:
        user_id, user_name = match.groups()
    else:
        send_error(f"Invalid user argument '{raw_arg}'. Does that user not exist, or did you forget to @-mention them?")
    return user_id, user_name

def extract_info(http_post_data: Any) -> Request:
    action = require_field("command", http_post_data)
    action = action[1:]  # Remove leading "/" character

    args_str = require_field("text", http_post_data)
    args = args_str.strip().split(" ")  # replace "arg1 arg2" with ["arg1", "arg2"],
    args = [a for a in args if a != ""]  # filter out empty-string args

    requester_id = require_field("user_id", http_post_data)

    return Request(action, args, requester_id)


def require_field(field: str, data: dict[str, Any], err_msg: str = "") -> str:
    if field not in data:
        send_error(f"Failed to parse HTTP request: Could not find field '{field}'. {err_msg}")
        exit(1)
    value = data[field]
    if not isinstance(value, str):
        send_error(f"Failed to parse HTTP request: Field '{field}' is not a string.")
        exit(1)
    return value


def parse_args(args: list[str]) -> tuple[Any, Any]:
    if len(args) < 3:
        send_error("Not enough arguments provided to the script.")

    try:
        http_get_data = json.loads(args[1])
        http_post_data = json.loads(args[2])
    except json.JSONDecodeError as e:
        send_error(f"Error decoding JSON arguments: {e}")

    return http_get_data, http_post_data


def run(argv: list[str]) -> None:
    http_get_data, http_post_data = parse_args(argv)
    request = extract_info(http_post_data)
    run_action(request)


if __name__ == "__main__":
    run(sys.argv)
