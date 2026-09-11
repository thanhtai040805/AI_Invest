import { io, type Socket } from "socket.io-client"
import { tokenStore } from "./api/client"
let socket: Socket | null = null
export function getSocket() { if (!socket) socket = io(process.env.NEXT_PUBLIC_SOCKET_URL || "http://localhost:3001", { autoConnect: false, transports: ["websocket", "polling"], auth: callback => callback({ token: tokenStore.get() }) }); return socket }
