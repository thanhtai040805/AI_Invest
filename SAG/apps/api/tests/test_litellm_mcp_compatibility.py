"""Offline regression: ordinary function tools must stream without MCP v2 imports.

Also runnable in the production image (no pytest or provider credentials needed):
    python - < tests/test_litellm_mcp_compatibility.py
"""

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer


class LiteLLMMCPCompatibilityTest(unittest.TestCase):
    def test_function_tools_stream_without_mcp_gateway_dependencies(self):
        class OpenAIStub(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.received_tools = body.get("tools")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunk = {
                    "id": "offline-test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "offline-model",
                    "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": None}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), OpenAIStub)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        async def ask():
            from litellm import acompletion

            stream = await acompletion(
                model="openai/offline-model",
                api_key="offline-test-key",
                api_base=f"http://127.0.0.1:{server.server_port}/v1",
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
                max_retries=0,
                timeout=10,
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "project_search",
                        "description": "Search this project",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }],
            )
            return "".join([chunk.choices[0].delta.content or "" async for chunk in stream])

        try:
            self.assertEqual(asyncio.run(ask()), "OK")
            self.assertTrue(server.received_tools)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
