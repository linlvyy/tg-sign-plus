import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: any[]) {
  return twMerge(clsx(inputs));
}

const CSRF_COOKIE_NAME = "tg-signer-csrf";
export const CSRF_HEADER_NAME = "X-CSRF-Token";

const getCookie = (name: string): string | null => {
  if (typeof document === "undefined") return null;
  const encodedName = `${encodeURIComponent(name)}=`;
  const item = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(encodedName));
  return item ? decodeURIComponent(item.slice(encodedName.length)) : null;
};

export const csrfHeaders = (): Record<string, string> => {
  const token = getCookie(CSRF_COOKIE_NAME);
  return token ? { [CSRF_HEADER_NAME]: token } : {};
};
