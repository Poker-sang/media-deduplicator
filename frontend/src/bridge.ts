import type { PathKind, Settings, StateUpdate, WindowAction } from './types';

interface BridgeError { error: string }
interface Success { ok: true }

interface Commands {
  get_settings: { args: []; result: Settings };
  save_settings: { args: [values: Settings]; result: Success };
  get_state: { args: [revision: number]; result: StateUpdate };
  choose_path: { args: [kind: PathKind, current: string]; result: string | null };
  start_scan: { args: [settings: Settings]; result: Success };
  cancel_scan: { args: []; result: Success };
  clear_results: { args: []; result: Success };
  dismiss_errors: { args: []; result: Success };
  get_preview: { args: [fileId: string]; result: string };
  open_file: { args: [fileId: string, folder: boolean]; result: Success };
  recycle: { args: [fileIds: string[]]; result: { message: string } };
  window_action: { args: [action: WindowAction]; result: Success };
  drag_window: { args: []; result: Success };
  resize_window: { args: [width: number, height: number]; result: Success };
}

type CommandName = keyof Commands;
type DesktopAPI = {
  [K in CommandName]: (...args: Commands[K]['args']) => Promise<Commands[K]['result'] | BridgeError>;
};

declare global {
  interface Window {
    pywebview?: { api: DesktopAPI };
  }
}

function isBridgeError(value: unknown): value is BridgeError {
  return value !== null && typeof value === 'object' && 'error' in value && typeof value.error === 'string';
}

export async function api<K extends CommandName>(method: K, ...args: Commands[K]['args']): Promise<Commands[K]['result']> {
  const bridge = window.pywebview?.api;
  if (!bridge) throw new Error('桌面服务尚未连接，请稍后重试');
  // Preserve the relationship between each method's arguments and result when indexing the bridge.
  const invoke = bridge[method] as (...params: Commands[K]['args']) => Promise<Commands[K]['result'] | BridgeError>;
  const value = await invoke(...args);
  if (isBridgeError(value)) throw new Error(value.error);
  return value;
}
