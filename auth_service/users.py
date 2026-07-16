from fastapi import HTTPException

users = {}

def create_user(user_id: str, email: str):
    if "@" not in email:
        raise HTTPException(
            status_code=400,
            detail="Invalid email"
        )

    users[user_id] = email
    return users[user_id]


def get_user(user_id: str):
    if user_id not in users:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )
    return users[user_id]


def update_user(user_id: str, email: str):
    if user_id not in users:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    users[user_id] = email
    return users[user_id]


def delete_user(user_id: str):
    users.pop(user_id, None)