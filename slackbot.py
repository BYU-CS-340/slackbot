# Copyright 2025 Brigham Young University. All rights reserved.

import sys
import json
import os
from typing import Any, Callable

import sqlite3


# Util functions


def build_queue_string(users: list[Any]) -> str:
    return "\n".join([f"{i}) <@{user}>" for i, user in enumerate(users)])


# Database, and folder it is in, must be writeable. This is why it is in a ./db folder.
# There is no error handling here - Use self.lastErrorMsg() to get error messages when things break.


class PersistentQueue:
    def __init__(self) -> None:
        db_name = "./db/queue.sqlite"
        db_exists = os.path.exists(db_name)

        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()

        # self.busyTimeout(3000)

        if not db_exists:
            os.chmod(db_name, 0o777)
            self.create_tables()

    def __del__(self) -> None:
        self.cursor.close()

    def create_tables(self) -> None:
        self.cursor.execute("CREATE TABLE IF NOT EXISTS queue(user TEXT NOT NULL);")
        self.conn.commit()

    def get_users_in_queue(self) -> list[str]:
        self.cursor.execute("SELECT * FROM queue;")
        results = self.cursor.fetchall()
        user_ids = [row[0] for row in results]
        return user_ids

    def get_postion_in_queue(self, user_id: str) -> int | None:
        users_in_queue = self.get_users_in_queue()
        position = 0
        while position < len(users_in_queue) and users_in_queue[position] != user_id:
            position += 1
        if position >= len(users_in_queue):
            return None
        return position

    def get_num_users_in_queue(self) -> int:
        users_in_queue = self.get_users_in_queue()
        return len(users_in_queue)

    def is_user_in_queue(self, user_id: str) -> bool:
        users_in_queue = self.get_users_in_queue()
        return user_id in users_in_queue

    def add_user_to_queue(self, user_id: str) -> int | None:
        position = self.get_postion_in_queue(user_id)
        if position is not None:
            # already in the queue
            return None
        self.cursor.execute("INSERT INTO queue VALUES (?);", [user_id])
        self.conn.commit()
        return self.get_postion_in_queue(user_id)

    def remove_user_from_queue(self, user_id: str) -> None:
        if self.is_user_in_queue(user_id):
            self.cursor.execute("DELETE FROM queue WHERE user = ?;", [user_id])
            self.conn.commit()

    def clear_queue(self) -> None:
        self.cursor.execute("DELETE FROM queue;")
        self.conn.commit()

    def next(self) -> str | None:
        # intentionally not doing a transaction here, unlikely to occur. simplify....
        users = self.get_users_in_queue()
        if not users:
            return None

        first = users[0]
        # also intentionally not handling edge cases (like user cant be removed from queue)
        self.remove_user_from_queue(first)
        return first

    def is_user_a_ta(self, user_id: str) -> bool:
        with open("./secrets/ta_slack_user_ids.json", "r") as f:
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


def handle_wait(req: Request) -> None:
    p = PersistentQueue()
    position = p.get_postion_in_queue(req.requester_id)
    size = p.get_num_users_in_queue()
    msg: str = f"There are {size} people in the queue!"
    if position is not None:
        msg += f" There are {position} people in front of you."
    send_message(msg)


def handle_passoff(req: Request) -> None:
    p = PersistentQueue()

    if p.is_user_in_queue(req.requester_id):
        # they were already in the queue OR we couldn't add them for some reason
        send_message(
            "Couldn't add you to the queue; you're already in it! Patience grasshopper, we'll get to you soon."
        )
        return

    position = p.add_user_to_queue(req.requester_id)

    if position is None:
        send_message(
            "Couldn't add you to the queue for some reason. Try again, and if the problem persists, contact a TA."
        )
        return

    send_message(f"You were added to the passoff queue. There are {position} people in front of you.", private=False)


def handle_nevermind(req: Request) -> None:
    p = PersistentQueue()
    if not p.is_user_in_queue(req.requester_id):
        send_message("Couldn't remove you from the queue. Were you in it?")
    else:
        p.remove_user_from_queue(req.requester_id)
        send_message("You were removed from the queue. Come back soon.")


def handle_next(req: Request) -> None:
    p = PersistentQueue()
    first = p.next()
    if first is None:
        send_message("Hmm, no one is in the queue, so nothing was done.")
        return
    else:
        send_message(
            f"You are up <@{first}>. Please come in, or DM <@{req.requester_id}> the link to your Zoom meeting.", private=False
        )
        return


def handle_queue(req: Request) -> None:
    p = PersistentQueue()
    users = p.get_users_in_queue()
    if not users:
        send_message("Ain't nobody here.")
        return
    else:
        size = p.get_num_users_in_queue()
        numbered_users_list = build_queue_string(users)
        send_message(f"There are {size} people in the queue.\n{numbered_users_list}")
        return


def handle_clear_queue(req: Request) -> None:
    p = PersistentQueue()
    users = p.get_users_in_queue()
    if not users:
        send_message("Ain't nobody here.")
        return

    size = p.get_num_users_in_queue()
    numbered_users_list = build_queue_string(users)

    p.clear_queue()

    send_message(f"The following {size} people were cleared from the queue:\n{numbered_users_list}", private=False)
    return


def handle_close_queue(req: Request) -> None:
    p = PersistentQueue()
    users = p.get_users_in_queue()
    numbered_users_list = build_queue_string(users)
    send_message(
        "Closing the queue for the night.\n\nSorry we couldn't get to everyone, "
        "but don't worry--if you were on the queue an hour before the official closing time "
        "(according to the schedule, not this message), you can still get credit tomorrow "
        "as if you had passed off today."
        f"\n\n{numbered_users_list}"
        "\n\n'Night, y'all!",
        private=False,
    )
    p.clear_queue()
    return


def run_action(req: Request) -> None:
    student_handlers: dict[str, Callable[[Request], None]] = {
        "wait": handle_wait,
        "passoff": handle_passoff,
        "nevermind": handle_nevermind,
        "queue": handle_queue,
    }

    ta_only_handlers: dict[str, Callable[[Request], None]] = {
        "next": handle_next,
        "clearqueue": handle_clear_queue,
        "closequeue": handle_close_queue,
    }

    if req.action in student_handlers:
        handler = student_handlers[req.action]
        handler(req)
        return

    if req.action in ta_only_handlers:
        p = PersistentQueue()
        if not p.is_user_a_ta(req.requester_id):
            send_message("TA command only, sorry.")
            return
        handler = ta_only_handlers[req.action]
        handler(req)
        return

    send_error(f"Unrecognized action: '{req.action}'. Args were '{req.args}'")


def send_message(msg: str, private: bool = True) -> None:
    res = {"text": msg} if private else {"text": msg, "response_type": "in_channel"}
    res_str = json.dumps(res)
    print(res_str)


def send_error(msg: str) -> None:
    send_message(f"Error (please contact a TA!): {msg}")
    sys.exit(1)


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
