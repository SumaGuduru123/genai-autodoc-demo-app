import axios from "axios";

const api = axios.create({
    baseURL: "http://localhost:8000"
});

export async function login(username: string, password: string) {
    return api.post("/login", {
        username,
        password
    });
}

export async function refresh(username: string) {
    return api.post("/refresh", {
        username
    });
}