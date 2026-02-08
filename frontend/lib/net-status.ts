import { useEffect, useState } from "react";
import NetInfo, { NetInfoState } from "@react-native-community/netinfo";
import { syncEvents } from "./sync-events";

class NetworkStatus {
  isOnline = true;
  private listeners: Set<(online: boolean) => void> = new Set();
  private unsubscribe: (() => void) | null = null;

  start() {
    if (this.unsubscribe) return;
    this.unsubscribe = NetInfo.addEventListener((state: NetInfoState) => {
      const online = !!(state.isConnected && state.isInternetReachable !== false);
      if (online !== this.isOnline) {
        this.isOnline = online;
        this.listeners.forEach((cb) => cb(online));
        if (online) {
          syncEvents.emit("online");
        }
      }
    });
  }

  stop() {
    this.unsubscribe?.();
    this.unsubscribe = null;
  }

  subscribe(cb: (online: boolean) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
}

export const netStatus = new NetworkStatus();

export function useNetStatus(): boolean {
  const [online, setOnline] = useState(netStatus.isOnline);
  useEffect(() => netStatus.subscribe(setOnline), []);
  return online;
}
