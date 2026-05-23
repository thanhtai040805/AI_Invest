/**
 * API client for the AIInvest backend
 * Connects to the FastAPI backend with multi-model orchestration
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = {
  /**
   * Send message to agent run endpoint
   */
  async sendMessage(sessionId: string, message: string) {
    const response = await fetch(`${API_BASE_URL}/agent/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: message,
        stream: true,
      }),
    });
    if (!response.ok) throw new Error("Failed to send message");
    return response.json();
  },

  /**
   * Get SSE URL for streaming
   */
  sseUrl(sessionId: string): string {
    return `${API_BASE_URL}/agent/run/${sessionId}/stream`;
  },

  /**
   * Create a new session
   */
  async createSession(initialPrompt: string) {
    const response = await fetch(`${API_BASE_URL}/agent/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: initialPrompt,
        stream: true,
      }),
    });
    if (!response.ok) throw new Error("Failed to create session");
    return response.json();
  },

  /**
   * Cancel a session
   */
  async cancelSession(sessionId: string) {
    const response = await fetch(`${API_BASE_URL}/agent/cancel/${sessionId}`, {
      method: "POST",
    });
    if (!response.ok) throw new Error("Failed to cancel session");
    return response.json();
  },

  /**
   * Get session messages
   */
  async getSessionMessages(sessionId: string) {
    const response = await fetch(`${API_BASE_URL}/agent/sessions/${sessionId}/messages`);
    if (!response.ok) throw new Error("Failed to get session messages");
    return response.json();
  },

  /**
   * Upload file
   */
  async uploadFile(file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${API_BASE_URL}/agent/upload`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) throw new Error("Failed to upload file");
    return response.json();
  },

  /**
   * Get run detail (equity curve, metrics, trades)
   */
  async getRun(runId: string) {
    const response = await fetch(`${API_BASE_URL}/api/runs/${runId}`);
    if (!response.ok) throw new Error("Failed to get run");
    return response.json() as Promise<{
      equity_curve?: Array<{ time: string; equity: number }>;
      metrics?: Record<string, number>;
      trades?: Array<Record<string, unknown>>;
    }>;
  },

  /**
   * Get Pine Script code for a run
   */
  async getRunPine(runId: string) {
    const response = await fetch(`${API_BASE_URL}/api/runs/${runId}/pine`);
    if (!response.ok) return { exists: false, content: null };
    return response.json() as Promise<{ exists: boolean; content: string | null }>;
  },
};
