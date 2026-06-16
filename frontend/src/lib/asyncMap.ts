import type { Dispatch, SetStateAction } from "react";

export type AsyncMap<T, K extends string | number = string | number> = Record<K, T>;

export function setAsyncMapValue<T, K extends string | number>(
  setter: Dispatch<SetStateAction<AsyncMap<T, K>>>,
  key: K,
  value: NoInfer<T>,
): void {
  setter((prev) => ({ ...prev, [key]: value }));
}

export function deleteAsyncMapValue<T, K extends string | number>(
  setter: Dispatch<SetStateAction<AsyncMap<T, K>>>,
  key: K,
): void {
  setter((prev) => {
    const next = { ...prev };
    delete next[key];
    return next;
  });
}
