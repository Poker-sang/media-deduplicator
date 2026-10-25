export type MediaTab = 0 | 1;
export type ScanMode = 0 | 1 | 2;
export type Sensitivity = '严格' | '平衡' | '宽松';
export type PhotoPrefix = 'folder' | 'reference' | 'compare';
export type ScanPrefix = PhotoPrefix | `video_${PhotoPrefix}`;
export type PathKind = 'folder' | 'image' | 'video';
export type WindowAction = 'minimize' | 'maximize' | 'close';
export type DialogKind = 'recycle' | 'close' | 'errors' | null;

export interface SettingsPaths {
  photo_base_folder: string;
  reference_path: string;
  compare_second_folder: string;
  video_base_folder: string;
  video_reference_path: string;
  video_second_folder: string;
}

export type PathKey = keyof SettingsPaths;
export type PathField = [label: string, key: PathKey, kind: PathKind];
export type Settings = SettingsPaths & {
  media_tab: MediaTab;
  photo_mode: ScanMode;
  video_mode: ScanMode;
} & Record<`${ScanPrefix}_recursive`, boolean>
  & Record<`${ScanPrefix}_sensitivity`, Sensitivity>
  & Record<`${PhotoPrefix}_crop`, boolean>;

export interface MediaFile {
  id: string;
  name: string;
  path: string;
  size: number;
  dimensions: string;
  format: string;
  quality: number;
  recommended: boolean;
  stats: string;
}

export interface DuplicateGroup {
  id: string;
  index: number;
  files: MediaFile[];
  confidence: number;
  kind: string;
  reasons: string[];
}

export interface ScanState {
  scanning: boolean;
  cancelling: boolean;
  stage: string;
  detail: string;
  progress: number;
  groups: DuplicateGroup[];
  errors: string[];
  scanned: number;
  compared: number;
  elapsed: number;
  revision: number;
}

// Polls with an unchanged revision omit groups; the UI retains the last list.
export type StateUpdate = Omit<ScanState, 'groups'> & { groups?: DuplicateGroup[] };
