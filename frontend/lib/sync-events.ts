type Listener = () => void;

class SyncEvents {
  private listeners: Map<string, Set<Listener>> = new Map();

  on(event: string, cb: Listener): () => void {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(cb);
    return () => this.listeners.get(event)?.delete(cb);
  }

  emit(event: string): void {
    this.listeners.get(event)?.forEach((cb) => cb());
  }
}

export const syncEvents = new SyncEvents();
