import { TokenResponse } from "./types";
import { clearToken, getToken, refreshAccessToken } from "./auth";
import { CSRF_HEADER_NAME, csrfHeaders } from "./utils";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

const pathSegment = (value: string) => encodeURIComponent(value);

const toRecord = (headers?: HeadersInit): Record<string, string> => {
  if (!headers) return {};
  if (headers instanceof Headers) {
    return Object.fromEntries(headers.entries());
  }
  if (Array.isArray(headers)) {
    return Object.fromEntries(headers);
  }
  return headers as Record<string, string>;
};

const redirectToLogin = () => {
  clearToken();
  if (typeof window !== "undefined") {
    window.location.href = "/";
  }
};

const buildHeaders = (headers?: HeadersInit, token?: string | null, body?: BodyInit | null, method?: string) => {
  const mergedHeaders: Record<string, string> = {
    ...toRecord(headers),
  };
  if (!mergedHeaders["Content-Type"] && typeof body === "string") {
    mergedHeaders["Content-Type"] = "application/json";
  }
  if (token) {
    mergedHeaders["Authorization"] = `Bearer ${token}`;
  }
  const methodName = (method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(methodName) && !mergedHeaders[CSRF_HEADER_NAME]) {
    Object.assign(mergedHeaders, csrfHeaders());
  }
  return mergedHeaders;
};

const parseError = async (res: Response) => {
  let errorMessage = "请求失败";
  let errorCode: string | undefined;
  try {
    const errorData = await res.json();
    if (errorData && typeof errorData === "object") {
      errorMessage = errorData.detail || errorData.message || JSON.stringify(errorData);
      errorCode = errorData.code;
    } else {
      errorMessage = JSON.stringify(errorData);
    }
  } catch {
    try {
      errorMessage = await res.text() || "请求失败";
    } catch {
      // ignore
    }
  }
  const err: any = new Error(errorMessage);
  err.status = res.status;
  if (errorCode) {
    err.code = errorCode;
  }
  return err;
};

async function request<T>(
  path: string,
  options: RequestInit = {},
  allowRefresh: boolean = true
): Promise<T> {
  const currentToken = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: buildHeaders(options.headers, currentToken, options.body, options.method),
    cache: "no-store",
    credentials: "include",
  });

  if (res.status === 401 && currentToken && allowRefresh && !path.startsWith("/auth/refresh") && !path.startsWith("/auth/login") && !path.startsWith("/auth/logout")) {
    const refreshedToken = await refreshAccessToken();
    if (refreshedToken) {
      return request<T>(path, options, false);
    }
    redirectToLogin();
  }

  if (!res.ok) {
    if (res.status === 401 && path.startsWith("/auth/refresh")) {
      redirectToLogin();
    }
    throw await parseError(res);
  }
  if (res.status === 204) {
    return {} as T;
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return res.json();
  }
  return (await res.text()) as T;
}

// ============ 认证 ============

export const login = (payload: {
  username: string;
  password: string;
  totp_code?: string;
}) =>
  request<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  }, false);

export const getMe = () =>
  request("/auth/me");

export const resetTOTP = (payload: { username: string; password: string }) =>
  request<{ success: boolean; message: string }>("/auth/reset-totp", {
    method: "POST",
    body: JSON.stringify(payload),
  }, false);


// ============ 账号管理（重构版）============

export interface LoginStartRequest {
  account_name: string;
  phone_number: string;
  proxy?: string;
  chat_cache_ttl_minutes?: number;
}

export interface LoginStartResponse {
  phone_code_hash: string;
  phone_number: string;
  account_name: string;
  message: string;
}

export interface LoginVerifyRequest {
  account_name: string;
  phone_number: string;
  phone_code: string;
  phone_code_hash: string;
  password?: string;
  proxy?: string;
  chat_cache_ttl_minutes?: number;
}

export interface LoginVerifyResponse {
  success: boolean;
  user_id?: number;
  first_name?: string;
  username?: string;
  message: string;
}

export interface QrLoginStartRequest {
  account_name: string;
  proxy?: string;
  chat_cache_ttl_minutes?: number;
}

export interface QrLoginStartResponse {
  login_id: string;
  qr_uri: string;
  qr_image?: string | null;
  expires_at: string;
}

export interface QrLoginStatusResponse {
  status: string;
  expires_at?: string;
  message?: string;
  account?: AccountInfo | null;
  user_id?: number;
  first_name?: string;
  username?: string;
}

export interface QrLoginCancelResponse {
  success: boolean;
  message: string;
}

export interface QrLoginPasswordRequest {
  login_id: string;
  password: string;
}

export interface QrLoginPasswordResponse {
  success: boolean;
  message: string;
  account?: AccountInfo | null;
  user_id?: number;
  first_name?: string;
  username?: string;
}

export interface AccountInfo {
  name: string;
  session_file: string;
  exists: boolean;
  size: number;
  remark?: string | null;
  proxy?: string | null;
  chat_cache_ttl_minutes?: number;
}

export interface AccountStatusCheckRequest {
  account_names?: string[];
  timeout_seconds?: number;
}

export interface AccountStatusItem {
  account_name: string;
  ok: boolean;
  status: "connected" | "invalid" | "error" | "not_found" | string;
  message?: string;
  code?: string;
  checked_at?: string;
  needs_relogin?: boolean;
  user_id?: number;
}

export interface AccountStatusCheckResponse {
  results: AccountStatusItem[];
}

export const startAccountLogin = (data: LoginStartRequest) =>
  request<LoginStartResponse>("/accounts/login/start", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const verifyAccountLogin = (data: LoginVerifyRequest) =>
  request<LoginVerifyResponse>("/accounts/login/verify", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const listAccounts = () =>
  request<{ accounts: AccountInfo[]; total: number }>("/accounts");

export const checkAccountsStatus = (data: AccountStatusCheckRequest) =>
  request<AccountStatusCheckResponse>("/accounts/status/check", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const deleteAccount = (accountName: string) =>
  request<{ success: boolean; message: string }>(`/accounts/${pathSegment(accountName)}`, {
    method: "DELETE",
  });

export const checkAccountExists = (accountName: string) =>
  request<{ exists: boolean; account_name: string }>(`/accounts/${pathSegment(accountName)}/exists`);

export const updateAccount = (
  accountName: string,
  data: { remark?: string | null; proxy?: string | null; chat_cache_ttl_minutes?: number | null }
) =>
  request<{ success: boolean; message: string; account?: AccountInfo | null }>(`/accounts/${pathSegment(accountName)}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });

export const startQrLogin = (data: QrLoginStartRequest) =>
  request<QrLoginStartResponse>("/accounts/qr/start", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const getQrLoginStatus = (loginId: string) =>
  request<QrLoginStatusResponse>(`/accounts/qr/status?login_id=${encodeURIComponent(loginId)}`);

export const cancelQrLogin = (loginId: string) =>
  request<QrLoginCancelResponse>("/accounts/qr/cancel", {
    method: "POST",
    body: JSON.stringify({ login_id: loginId }),
  });

export const submitQrPassword = (data: QrLoginPasswordRequest) =>
  request<QrLoginPasswordResponse>("/accounts/qr/password", {
    method: "POST",
    body: JSON.stringify(data),
  });

// ============ 配置管理 ============

export const listConfigTasks = () =>
  request<{ sign_tasks: string[]; total: number }>("/config/tasks");

export const exportSignTask = (taskName: string, accountName?: string) => {
  const params = new URLSearchParams();
  if (accountName) params.append("account_name", accountName);
  const url = `/config/export/sign/${pathSegment(taskName)}${params.toString() ? `?${params.toString()}` : ""}`;
  return request<string>(url, {
    headers: {
      Accept: "text/plain",
    },
  });
};

export const importSignTask = (
  configJson: string,
  taskName?: string,
  accountName?: string
) =>
  request<{ success: boolean; task_name: string; message: string }>("/config/import/sign", {
    method: "POST",
    body: JSON.stringify({ config_json: configJson, task_name: taskName, account_name: accountName }),
  });

export const exportAllConfigs = () =>
  request<Record<string, unknown>>("/config/export/all");

export const importAllConfigs = (configJson: string, overwrite = false) =>
  request<{
    signs_imported: number;
    signs_skipped: number;
    errors: string[];
    message: string;
  }>("/config/import/all", {
    method: "POST",
    body: JSON.stringify({ config_json: configJson, overwrite }),
  });

export const deleteSignConfig = (taskName: string, accountName?: string) => {
  const params = new URLSearchParams();
  if (accountName) params.append("account_name", accountName);
  const url = `/config/sign/${pathSegment(taskName)}${params.toString() ? `?${params.toString()}` : ""}`;
  return request<{ success: boolean; message: string }>(url, {
    method: "DELETE",
  });
};


// ============ 用户设置 ============

export const changePassword = (oldPassword: string, newPassword: string) =>
  request<{ success: boolean; message: string }>("/user/password", {
    method: "PUT",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });

export const getTOTPStatus = () =>
  request<{ enabled: boolean; secret?: string }>("/user/totp/status");

export const setupTOTP = () =>
  request<{ enabled: boolean; secret: string }>("/user/totp/setup", {
    method: "POST",
  });

export const enableTOTP = (totpCode: string) =>
  request<{ success: boolean; message: string }>("/user/totp/enable", {
    method: "POST",
    body: JSON.stringify({ totp_code: totpCode }),
  });

export const disableTOTP = (totpCode: string) =>
  request<{ success: boolean; message: string }>("/user/totp/disable", {
    method: "POST",
    body: JSON.stringify({ totp_code: totpCode }),
  });

export const changeUsername = (newUsername: string, password: string) =>
  request<ChangeUsernameResponse>("/user/username", {
    method: "PUT",
    body: JSON.stringify({ new_username: newUsername, password: password }),
  });

// ============ AI 配置 ============

export interface AIConfig {
  has_config: boolean;
  base_url?: string;
  model?: string;
  api_key_masked?: string;
}

export interface ChangeUsernameResponse {
  success: boolean;
  message: string;
  access_token?: string;
}

export interface AITestResult {
  success: boolean;
  message: string;
  model_used?: string;
}

export const getAIConfig = () =>
  request<AIConfig>("/config/ai");

export const saveAIConfig = (
  config: { api_key?: string; base_url?: string; model?: string }
) =>
  request<{ success: boolean; message: string }>("/config/ai", {
    method: "POST",
    body: JSON.stringify(config),
  });

export const testAIConnection = () =>
  request<AITestResult>("/config/ai/test", {
    method: "POST",
  });

export const deleteAIConfig = () =>
  request<{ success: boolean; message: string }>("/config/ai", {
    method: "DELETE",
  });

// ============ 全局设置 ============

export interface GlobalSettings {
  sign_interval?: number | null;  // null 表示随机 1-120 秒
  log_retention_days?: number;    // 日志保留天数，默认 7
  data_dir?: string | null;
}

export const getGlobalSettings = () =>
  request<GlobalSettings>("/config/settings");

export const saveGlobalSettings = (settings: GlobalSettings) =>
  request<{ success: boolean; message: string }>("/config/settings", {
    method: "POST",
    body: JSON.stringify(settings),
  });

// ============ Telegram API 配置 ============

export interface TelegramConfig {
  api_id: string;
  api_hash_masked?: string | null;
  has_api_hash: boolean;
  is_custom: boolean;
  default_api_id: string;
  default_api_hash_masked?: string | null;
}

export const getTelegramConfig = () =>
  request<TelegramConfig>("/config/telegram");

export const saveTelegramConfig = (
  config: { api_id: string; api_hash: string }
) =>
  request<{ success: boolean; message: string }>("/config/telegram", {
    method: "POST",
    body: JSON.stringify(config),
  });

export const resetTelegramConfig = () =>
  request<{ success: boolean; message: string }>("/config/telegram", {
    method: "DELETE",
  });

// ============ 账号日志 ============

export interface AccountLog {
  id: number;
  account_name: string;
  task_name: string;
  message: string;
  summary?: string;
  bot_message?: string;
  success: boolean;
  created_at: string;
}

export const getAccountLogs = (accountName: string, limit: number = 100) =>
  request<AccountLog[]>(`/accounts/${pathSegment(accountName)}/logs?limit=${limit}`);

export const clearAccountLogs = (accountName: string) =>
  request<{ success: boolean; cleared: number; message: string; code?: string }>(
    `/accounts/${pathSegment(accountName)}/logs/clear`,
    { method: "POST" }
  );

export const exportAccountLogs = async (accountName: string) => {
  const res = await fetch(`${API_BASE}/accounts/${pathSegment(accountName)}/logs/export`, {
    headers: {
      Authorization: `Bearer ${getToken()}`,
    },
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Export failed");
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `logs_${accountName}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
};

// ============ 签到任务管理 ============

export type SignTaskAction =
  | { action: 1; text: string }
  | { action: 2; dice: string }
  | { action: 3; text: string }
  | { action: 4 }
  | { action: 5 }
  | {
      action: 6;
      caption_pattern?: string;
      captcha_lengths?: number[];
      captcha_charset?: string;
      captcha_case?: "preserve" | "upper" | "lower";
      reply_to_message?: boolean;
    }
  | { action: 7 }
  | { action: 8 }
  | {
      action: 9;
      keywords: string[];
      checked_keywords?: string[];
      retry_keywords?: string[];
      fail_keywords?: string[];
      account_fail_keywords?: string[];
      ignore_keywords?: string[];
    };

export interface SignTaskChat {
  chat_id: number;
  name: string;
  actions: SignTaskAction[];
  delete_after?: number;
  action_interval: number;
  event_timeout?: number;
  event_retries?: number;
  event_retry_wait?: number;
  event_history_limit?: number;
  event_action_timeout?: number;
  event_ai_fallback?: boolean;
}

export interface LastRunInfo {
  time: string;
  success: boolean;
  message?: string;
}

export interface SignTask {
  name: string;
  account_name: string;
  sign_at: string;
  chats: SignTaskChat[];
  random_seconds: number;
  sign_interval: number;
  retry_count: number;
  engine?: "legacy" | "event";
  enabled: boolean;
  last_run?: LastRunInfo | null;
  execution_mode?: "fixed" | "range";
  range_start?: string;
  range_end?: string;
  next_scheduled_at?: string | null;
}

export interface CreateSignTaskRequest {
  name: string;
  account_name: string;
  sign_at: string;
  chats: SignTaskChat[];
  random_seconds?: number;
  sign_interval?: number;
  retry_count?: number;
  engine?: "legacy" | "event";
  execution_mode?: "fixed" | "range";
  range_start?: string;
  range_end?: string;
}

export interface UpdateSignTaskRequest {
  sign_at?: string;
  chats?: SignTaskChat[];
  random_seconds?: number;
  sign_interval?: number;
  retry_count?: number;
  engine?: "legacy" | "event";
  execution_mode?: "fixed" | "range";
  range_start?: string;
  range_end?: string;
}

export interface ChatInfo {
  id: number;
  title?: string;
  username?: string;
  type: string;
  first_name?: string;
}

export interface ChatSearchResponse {
  items: ChatInfo[];
  total: number;
  limit: number;
  offset: number;
}

export interface ChatCacheResponse {
  items: ChatInfo[];
  last_cached_at?: string | null;
  cache_ttl_minutes: number;
  expired: boolean;
  count: number;
}

export async function listSignTasks(accountName?: string, forceRefresh?: boolean): Promise<SignTask[]> {
  const params = new URLSearchParams();
  if (accountName) params.append('account_name', accountName);
  if (forceRefresh) params.append('force_refresh', 'true');
  const url = `/sign-tasks${params.toString() ? `?${params.toString()}` : ''}`;
  return request<SignTask[]>(url);
}

export const getSignTask = (name: string, accountName?: string) => {
  const params = new URLSearchParams();
  if (accountName) params.append("account_name", accountName);
  const url = `/sign-tasks/${pathSegment(name)}${params.toString() ? `?${params.toString()}` : ""}`;
  return request<SignTask>(url);
};

export const createSignTask = (data: CreateSignTaskRequest) =>
  request<SignTask>("/sign-tasks", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const updateSignTask = (name: string, data: UpdateSignTaskRequest, accountName?: string) => {
  const params = new URLSearchParams();
  if (accountName) params.append("account_name", accountName);
  return request<SignTask>(`/sign-tasks/${pathSegment(name)}${params.toString() ? `?${params.toString()}` : ""}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
};

export const deleteSignTask = (name: string, accountName?: string) => {
  const params = new URLSearchParams();
  if (accountName) params.append("account_name", accountName);
  return request<{ ok: boolean }>(`/sign-tasks/${pathSegment(name)}${params.toString() ? `?${params.toString()}` : ""}`, {
    method: "DELETE",
  });
};

export const setSignTaskEnabled = (name: string, accountName: string, enabled: boolean) =>
  request<SignTask>(`/sign-tasks/${pathSegment(name)}/enabled`, {
    method: "PATCH",
    body: JSON.stringify({ account_name: accountName, enabled }),
  });

export const runSignTask = (name: string, accountName: string) => {
  const params = new URLSearchParams({ account_name: accountName });
  return request<{ success: boolean; output: string; error: string; started: boolean; code: string }>(`/sign-tasks/${pathSegment(name)}/run?${params.toString()}`, {
    method: "POST",
  });
};

export const getAccountChats = (
  accountName: string,
  options?: { forceRefresh?: boolean; autoRefreshIfExpired?: boolean; ensureExists?: boolean }
) => {
  const params = new URLSearchParams();
  if (options?.forceRefresh) params.append("force_refresh", "true");
  if (options?.autoRefreshIfExpired) params.append("auto_refresh_if_expired", "true");
  if (options?.ensureExists) params.append("ensure_exists", "true");
  const query = params.toString();
  return request<ChatCacheResponse>(`/sign-tasks/chats/${pathSegment(accountName)}${query ? `?${query}` : ""}`);
};

export const refreshAccountChats = (accountName: string) =>
  request<ChatCacheResponse>(`/sign-tasks/chats/${pathSegment(accountName)}/refresh`, {
    method: "POST",
  });

export const getAccountChatCacheMeta = (accountName: string) =>
  request<Pick<ChatCacheResponse, "last_cached_at" | "cache_ttl_minutes" | "expired" | "count"> & { account_name: string }>(`/sign-tasks/chats/${pathSegment(accountName)}/meta`);

export const searchAccountChats = (
  accountName: string,
  query: string,
  limit: number = 50,
  offset: number = 0
) => {
  const params = new URLSearchParams();
  params.append("q", query);
  params.append("limit", String(limit));
  params.append("offset", String(offset));
  return request<ChatSearchResponse>(`/sign-tasks/chats/${pathSegment(accountName)}/search?${params.toString()}`);
};

export const getSignTaskLogs = (name: string, accountName?: string) => {
    const params = new URLSearchParams();
    if (accountName) params.append("account_name", accountName);
    const url = `/sign-tasks/${pathSegment(name)}/logs${params.toString() ? `?${params.toString()}` : ""}`;
    return request<string[]>(url);
};

export interface SchedulerSignTaskStatus {
  job_id: string;
  account_name: string;
  task_name: string;
  enabled: boolean;
  execution_mode: "fixed" | "range" | string;
  schedule: string;
  next_run?: string | null;
  next_scheduled_at?: string | null;
  effective_next_run?: string | null;
  execution_job_exists: boolean;
  job_exists: boolean;
}

export interface SchedulerStatus {
  timezone: string;
  running: boolean;
  total_jobs: number;
  sign_job_count: number;
  sign_tasks: SchedulerSignTaskStatus[];
}

export const getSchedulerStatus = (accountName?: string) => {
  const params = new URLSearchParams();
  if (accountName) params.append("account_name", accountName);
  return request<SchedulerStatus>(`/sign-tasks/scheduler/status${params.toString() ? `?${params.toString()}` : ""}`);
};

export interface SignTaskFlowItem {
  ts: string;
  level: "info" | "warning" | "error" | "success" | string;
  stage: "task" | "session" | "preheat" | "action" | "message" | "result" | string;
  event: string;
  text: string;
  meta?: Record<string, string | number | boolean | null>;
}

export interface SignTaskHistoryItem {
  time: string;
  success: boolean;
  message?: string;
  flow_logs?: string[];
  flow_items?: SignTaskFlowItem[];
  flow_truncated?: boolean;
  flow_line_count?: number;
  diagnostics?: {
    status: "pass" | "warn" | "fail" | "unknown" | string;
    summary: string;
    checks: Array<{
      id: string;
      label: string;
      status: "pass" | "warn" | "fail" | "skip" | "unknown" | string;
      detail?: string;
    }>;
    milestones: Record<string, string | number | boolean | null>;
  };
}

export interface SignTaskCanaryRun {
  status: "pass" | "warn" | "fail" | "missing" | "stale" | "unknown" | string;
  success: boolean;
  time?: string;
  age_hours?: number | null;
  fresh?: boolean;
  message?: string;
  diagnostics?: SignTaskHistoryItem["diagnostics"];
}

export interface SignTaskCanaryCheck {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "skip" | "unknown" | string;
  detail?: string;
}

export interface SignTaskCanaryTask {
  task_name: string;
  account_name: string;
  engine: string;
  status: "pass" | "warn" | "fail" | "missing" | "stale" | "unknown" | string;
  config_status?: "pass" | "warn" | "fail" | "missing" | "stale" | "unknown" | string;
  run_status?: "pass" | "warn" | "fail" | "missing" | "stale" | "unknown" | string;
  config_checks?: SignTaskCanaryCheck[];
  latest_time?: string;
  latest_summary?: string;
  runs: SignTaskCanaryRun[];
}

export interface SignTaskCanaryTarget {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "missing" | "unconfigured" | "stale" | "unknown" | string;
  summary: string;
  expected?: {
    chat_ids?: number[];
    names?: string[];
  };
  tasks: SignTaskCanaryTask[];
}

export interface SignTaskCanaryReport {
  status: "pass" | "warn" | "fail" | "missing" | "unknown" | string;
  generated_at?: string;
  max_age_hours?: number | null;
  task_count?: number;
  targets: SignTaskCanaryTarget[];
}

export const getSignTaskCanaryReport = (
  accountName?: string,
  historyLimit: number = 1,
  maxAgeHours?: number
) => {
  const params = new URLSearchParams();
  if (accountName) params.append("account_name", accountName);
  params.append("history_limit", String(historyLimit));
  if (maxAgeHours !== undefined) params.append("max_age_hours", String(maxAgeHours));
  return request<SignTaskCanaryReport>(
    `/sign-tasks/canary/report?${params.toString()}`
  );
};

export const getSignTaskHistory = (
  name: string,
  accountName: string,
  limit: number = 20
) => {
  const params = new URLSearchParams();
  params.append("account_name", accountName);
  params.append("limit", String(limit));
  return request<SignTaskHistoryItem[]>(
    `/sign-tasks/${pathSegment(name)}/history?${params.toString()}`
  );
};
