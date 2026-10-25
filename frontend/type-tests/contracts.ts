// Checked by tsc, never imported into the application or executed.
import { api } from '../src/bridge';
import type { Settings, StateUpdate } from '../src/types';

export function checkContracts(settings: Settings, state: StateUpdate): void {
  api('get_preview', 'file-id') satisfies Promise<string>;
  api('choose_path', 'folder', settings.photo_base_folder) satisfies Promise<string | null>;
  api('start_scan', settings) satisfies Promise<{ ok: true }>;
  state.groups?.forEach(group => group.files.forEach(file => file.name satisfies string));

  // @ts-expect-error Unknown desktop commands must be rejected.
  api('delete_file', 'file-id');
  // @ts-expect-error File IDs must be strings.
  api('get_preview', 42);
  // @ts-expect-error Resize requires both dimensions.
  api('resize_window', 1280);
  // @ts-expect-error Only supported path kinds are accepted.
  api('choose_path', 'audio', '');
  // @ts-expect-error There are only three scan modes.
  settings.photo_mode = 3;
  // @ts-expect-error Crop options are booleans.
  settings.folder_crop = 'true';
  // @ts-expect-error Crop comparison is not a video option.
  settings.video_compare_crop = true;
  // @ts-expect-error Unchanged revision responses can omit groups.
  state.groups.length;
}
