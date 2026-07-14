import { login, refresh } from "./apiClient";

export async function authenticate(
    username: string,
    password: string
) {
    return login(username, password);
}

export async function refreshSession(
    username: string
) {
    return refresh(username);
}