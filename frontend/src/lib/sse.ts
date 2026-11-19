import { apiResponse } from "../api/client";

export interface StreamHandlers {
  onEvent: (event: string, data: unknown) => void;
  onError?: (error: unknown) => void;
}

/** Parses Server-Sent Events from a text stream. Exported for testing. */
export class SseParser {
  private buffer = "";
  private event = "message";
  private data: string[] = [];

  constructor(private readonly emit: (event: string, data: unknown) => void) {}

  feed(chunk: string): void {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline !== -1) {
      this.line(this.buffer.slice(0, newline).replace(/\r$/, ""));
      this.buffer = this.buffer.slice(newline + 1);
      newline = this.buffer.indexOf("\n");
    }
  }

  private line(line: string): void {
    if (line === "") {
      if (this.data.length) this.emit(this.event, JSON.parse(this.data.join("\n")));
      this.event = "message";
      this.data = [];
    } else if (line.startsWith("event:")) {
      this.event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      this.data.push(line.slice(5).trim());
    }
  }
}

/** EventSource cannot send an Authorization header, and a token in the URL would
 *  end up in logs, so the stream is read through fetch. It reconnects with backoff
 *  until the caller aborts it. */
export async function streamEvents(path: string, handlers: StreamHandlers, signal: AbortSignal): Promise<void> {
  let delay = 1000;
  while (!signal.aborted) {
    let finished = false;
    try {
      const response = await apiResponse(path, { signal, headers: { Accept: "text/event-stream" } });
      if (!response.body) return;
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      const parser = new SseParser((event, data) => {
        delay = 1000;
        if (event === "done") finished = true;
        handlers.onEvent(event, data);
      });
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        parser.feed(value);
      }
      if (finished) return;
    } catch (error) {
      if (signal.aborted) return;
      handlers.onError?.(error);
    }
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(delay * 2, 15_000);
  }
}
