import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as Dialog from '@radix-ui/react-dialog';
import { Layers2, Images, Film, Folder, FolderOpen, FileImage, ChevronRight, ChevronDown, Check, Play, Square, Minus, X, Copy, ShieldCheck, ScanLine, CircleCheck, SlidersHorizontal, Trash2, CheckCheck, RotateCcw, ExternalLink, Image as ImageIcon, AlertCircle, LoaderCircle, Search, HardDrive, Maximize2, Info } from 'lucide-react';
import './style.css';

const defaults = {
  photo_base_folder: '', folder_recursive: true, folder_crop: false, folder_sensitivity: '平衡',
  reference_path: '', reference_recursive: true, reference_crop: false, reference_sensitivity: '平衡',
  compare_second_folder: '', compare_recursive: true, compare_crop: false, compare_sensitivity: '平衡',
  video_base_folder: '', video_folder_recursive: true, video_folder_sensitivity: '平衡',
  video_reference_path: '', video_reference_recursive: true, video_reference_sensitivity: '平衡',
  video_second_folder: '', video_compare_recursive: true, video_compare_sensitivity: '平衡',
  media_tab: 0, photo_mode: 0, video_mode: 0,
};
const emptyState = { scanning: false, stage: '准备就绪', detail: '选择路径，然后开始扫描', progress: 0, groups: [], errors: [], scanned: 0, compared: 0, elapsed: 0, revision: 0 };
const fmt = n => { if (!n) return '0 B'; const u = ['B','KB','MB','GB','TB']; const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), 4); return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`; };
const api = async (method, ...args) => {
  if (!window.pywebview?.api) throw new Error('桌面服务尚未连接，请稍后重试');
  const value = await window.pywebview.api[method](...args);
  if (value?.error) throw new Error(value.error);
  return value;
};
const keyFor = (photo, mode) => photo ? ['folder','reference','compare'][mode] : ['video_folder','video_reference','video_compare'][mode];

function App() {
  const [settings, setSettings] = useState(defaults);
  const [ready, setReady] = useState(false);
  const [state, setState] = useState(emptyState);
  const [groupId, setGroupId] = useState(null);
  const [fileId, setFileId] = useState(null);
  const [marks, setMarks] = useState(new Set());
  const [query, setQuery] = useState('');
  const [notice, setNotice] = useState('');
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [about, setAbout] = useState(false);
  const revision = useRef(-1);
  const saved = useRef('');
  const saveQueue = useRef(Promise.resolve());
  const markRef = useRef(marks);
  markRef.current = marks;
  const photo = settings.media_tab === 0;
  const mode = photo ? settings.photo_mode : settings.video_mode;
  const prefix = keyFor(photo, mode);
  const group = state.groups.find(g => g.id === groupId) || state.groups[0];
  const current = group?.files.find(f => f.id === fileId) || group?.files.find(f => !f.recommended) || group?.files[0];
  const winner = group?.files.find(f => f.recommended);
  const allFiles = [...new Map(state.groups.flatMap(g => g.files).map(f => [f.id, f])).values()];
  const markedFiles = allFiles.filter(f => marks.has(f.id));
  const markedSize = markedFiles.reduce((n, f) => n + f.size, 0);
  const visibleGroups = state.groups.filter(g => !query || g.files.some(f => f.name.toLowerCase().includes(query.toLowerCase())));
  const update = (key, value) => setSettings(s => ({ ...s, [key]: value }));
  const fail = e => setNotice(e.message || String(e));

  useEffect(() => {
    let disposed = false;
    const initialize = async () => {
      try {
        const data = await api('get_settings');
        if (disposed) return;
        const merged = { ...defaults, ...data };
        saved.current = JSON.stringify(merged);
        setSettings(merged); setReady(true);
      } catch (e) { if (!disposed) fail(e); }
    };
    if (window.pywebview?.api) initialize();
    else window.addEventListener('pywebviewready', initialize, { once: true });
    return () => { disposed = true; window.removeEventListener('pywebviewready', initialize); };
  }, []);

  useEffect(() => {
    if (!ready) return;
    const data = JSON.stringify(settings);
    if (data === saved.current) return;
    // Persist immediately: closing the window after an edit cannot lose a debounced write.
    saved.current = data;
    saveQueue.current = saveQueue.current.catch(() => {}).then(() => api('save_settings', settings)).catch(fail);
  }, [settings, ready]);

  useEffect(() => {
    if (!ready) return;
    let disposed = false, timeout;
    const poll = async () => {
      try {
        const next = await api('get_state', revision.current);
        if (disposed) return;
        if (next.groups) {
          revision.current = next.revision;
          setMarks(new Set()); setGroupId(next.groups[0]?.id ?? null); setFileId(null);
          setState(next);
        } else setState(s => ({ ...s, ...next }));
      } catch (e) { if (!disposed) fail(e); }
      if (!disposed) timeout = setTimeout(poll, 300);
    };
    poll();
    return () => { disposed = true; clearTimeout(timeout); };
  }, [ready]);

  const changeMedia = value => {
    if (state.scanning || value === settings.media_tab) return;
    update('media_tab', value);
    api('clear_results').catch(fail); setNotice(''); setQuery('');
  };
  const toggleMark = id => setMarks(m => { const copy = new Set(m); copy.has(id) ? copy.delete(id) : copy.add(id); return copy; });
  const recommend = groups => {
    setMarks(previous => {
      const copy = new Set(previous);
      groups.forEach(g => g.files.forEach(f => f.recommended ? copy.delete(f.id) : copy.add(f.id)));
      return copy;
    });
  };
  const start = async () => {
    setNotice('');
    try { await saveQueue.current; await api('start_scan', settings); } catch (e) { fail(e); }
  };
  const browse = async (key, kind) => {
    try { const path = await api('choose_path', kind, settings[key]); if (path) update(key, path); } catch (e) { fail(e); }
  };
  const recycle = async () => {
    setBusy(true);
    try { const result = await api('recycle', markedFiles.map(f => f.id)); setDialog(null); setNotice(result.message); } catch (e) { fail(e); setDialog(null); }
    finally { setBusy(false); }
  };
  const windowAction = async op => {
    try { if (op === 'close') { await saveQueue.current; await api('save_settings', settings); } await api('window_action', op); } catch (e) { fail(e); }
  };
  const startDrag = event => {
    if (event.button !== 0) return;
    const handle = event.currentTarget, pointerId = event.pointerId;
    const startX = event.screenX, startY = event.screenY;
    handle.setPointerCapture(pointerId);
    const end = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', end);
      handle.removeEventListener('pointercancel', end);
      handle.removeEventListener('lostpointercapture', end);
      if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
    };
    const move = e => {
      if (Math.hypot(e.screenX - startX, e.screenY - startY) < 4) return;
      end();
      api('drag_window').catch(fail);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
    handle.addEventListener('lostpointercapture', end);
  };
  const startResize = event => {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.screenX, startY = event.screenY, width = window.innerWidth, height = window.innerHeight;
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    let latest = [width, height], pending = false, stopped = false;
    const send = async () => {
      if (pending) return;
      pending = true;
      const size = latest;
      try { await api('resize_window', ...size); } catch (e) { fail(e); }
      pending = false;
      if (!stopped && size !== latest) send();
    };
    const move = e => { latest = [Math.max(1000, width + e.screenX - startX), Math.max(680, height + e.screenY - startY)]; send(); };
    const end = () => { stopped = true; handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', end); handle.removeEventListener('pointercancel', end); };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  };

  let paths;
  if (photo) paths = mode === 0 ? [['基准文件夹','photo_base_folder','folder']] : mode === 1 ? [['基准文件夹','photo_base_folder','folder'],['基准图片','reference_path','image']] : [['基准文件夹','photo_base_folder','folder'],['对比文件夹','compare_second_folder','folder']];
  else paths = mode === 0 ? [['基准文件夹','video_base_folder','folder']] : mode === 1 ? [['基准文件夹','video_base_folder','folder'],['基准视频','video_reference_path','video']] : [['基准文件夹','video_base_folder','folder'],['对比文件夹','video_second_folder','folder']];

  return <div className="app-shell">
    <header className="titlebar">
      <div className="brand" onPointerDown={startDrag} onDoubleClick={() => windowAction('maximize')}><div className="app-icon"><Layers2 size={17}/></div><span>本地媒体去重</span><span className="version">1.0</span></div>
      <div className="drag-space" onPointerDown={startDrag} onDoubleClick={() => windowAction('maximize')}/>
      <div className="window-controls">
        <button aria-label="最小化" onClick={() => windowAction('minimize')}><Minus size={15}/></button>
        <button aria-label="最大化或还原" onClick={() => windowAction('maximize')}><Square size={12}/></button>
        <button aria-label="关闭" className="window-close" onClick={() => state.scanning ? setDialog('close') : windowAction('close')}><X size={17}/></button>
      </div>
    </header>

    <main>
      <section className="setup">
        <div className="setup-top">
          <div className="media-switch" role="tablist" aria-label="媒体类型">
            <button role="tab" aria-selected={photo} className={photo ? 'active' : ''} disabled={state.scanning} onClick={() => changeMedia(0)}><Images size={16}/>照片</button>
            <button role="tab" aria-selected={!photo} className={!photo ? 'active' : ''} disabled={state.scanning} onClick={() => changeMedia(1)}><Film size={16}/>视频</button>
          </div>
          <div className="mode-switch" role="tablist" aria-label="扫描模式">
            {['文件夹内去重', photo ? '单图对比文件夹' : '单视频对比文件夹','文件夹对比文件夹'].map((label,i) => <button role="tab" aria-selected={mode === i} key={i} disabled={state.scanning} className={mode === i ? 'active' : ''} onClick={() => update(photo ? 'photo_mode' : 'video_mode', i)}>{label}</button>)}
          </div>
          <button className="icon-button info-button" title="使用帮助" aria-label="使用帮助" onClick={() => setAbout(true)}><Info size={17}/></button>
        </div>

        <div className="path-fields">
          {paths.map(([label,key,kind]) => <div className="path-row" key={key}>
            <label htmlFor={key}>{label}</label>
            <div className="path-input"><span>{kind === 'folder' ? <Folder size={16}/> : photo ? <FileImage size={16}/> : <Film size={16}/>}</span><input id={key} disabled={state.scanning} spellCheck={false} value={settings[key]} onChange={e => update(key, e.target.value)} placeholder={kind === 'folder' ? '输入或选择文件夹路径' : '输入或选择基准文件路径'}/></div>
            <button className="button browse-button" disabled={state.scanning} onClick={() => browse(key, kind)}><FolderOpen size={15}/>浏览</button>
          </div>)}
        </div>

        <div className="options-row">
          <div className="options-left">
            <label className="checkbox-label"><input type="checkbox" disabled={state.scanning} checked={settings[`${prefix}_recursive`]} onChange={e => update(`${prefix}_recursive`, e.target.checked)}/><span className="check-box"><Check size={11}/></span>包含子文件夹</label>
            {photo && <label className="checkbox-label" title="检测局部裁剪及构图变化，扫描耗时更长"><input type="checkbox" disabled={state.scanning} checked={settings[`${prefix}_crop`]} onChange={e => update(`${prefix}_crop`, e.target.checked)}/><span className="check-box"><Check size={11}/></span>裁剪比较<span className="subtle-badge">较慢</span></label>}
          </div>
          <div className="sensitivity"><SlidersHorizontal size={13}/><span>{photo ? '匹配灵敏度' : '视频相似度'}</span><div className="small-segment" role="group" aria-label="灵敏度">{['严格','平衡','宽松'].map(value => <button key={value} disabled={state.scanning} className={settings[`${prefix}_sensitivity`] === value ? 'active' : ''} onClick={() => update(`${prefix}_sensitivity`, value)}>{value}</button>)}</div></div>
        </div>

        <div className="scan-bar">
          <div className="scan-status"><span className={`status-dot ${state.scanning ? 'running' : state.stage === '扫描完成' ? 'complete' : ''}`}/><strong>{!ready ? '正在连接桌面服务…' : state.stage}</strong><span className="scan-detail" title={state.detail}>{state.detail}</span>{state.elapsed > 0 && <span className="elapsed">{state.elapsed.toFixed(1)} 秒</span>}</div>
          {state.scanning ? <button className="button cancel-button" disabled={state.cancelling} onClick={() => api('cancel_scan').catch(fail)}><Square size={13}/>{state.cancelling ? '正在停止…' : '停止扫描'}</button> : <button className="button primary-button" disabled={!ready || busy} onClick={start}><ScanLine size={16}/>开始扫描</button>}
          <div className="progress-track"><div style={{ width: `${Math.max(0, Math.min(100, state.progress))}%` }}/></div>
        </div>
      </section>

      {(notice || state.errors.length > 0) && <div className="notice" role="status"><AlertCircle size={15}/><span>{notice || `有 ${state.errors.length} 个文件未能读取`}</span>{state.errors.length > 0 && <button onClick={() => setDialog('errors')}>查看详情</button>}<button className="icon-button" aria-label="关闭提示" onClick={() => { setNotice(''); api('dismiss_errors').catch(fail); }}><X size={14}/></button></div>}

      <section className="workspace">
        <aside className="groups-panel">
          <div className="panel-heading"><h2>重复分组</h2><span className="count-badge">{state.groups.length}</span></div>
          <div className="group-search"><Search size={14}/><input aria-label="搜索分组文件名" placeholder="搜索文件名" value={query} onChange={e => setQuery(e.target.value)}/>{query && <button aria-label="清空搜索" onClick={() => setQuery('')}><X size={13}/></button>}</div>
          <div className="group-list">
            {visibleGroups.map((g) => <button className={`group-card ${g.id === group?.id ? 'selected' : ''}`} key={g.id} onClick={() => { setGroupId(g.id); setFileId(null); }}>
              <div className="group-card-top"><span className="group-symbol">{photo ? <Images size={17}/> : <Film size={17}/>}</span><strong>第 {g.index + 1} 组</strong><span className="group-files">{g.files.length} 个文件</span></div>
              <div className="group-card-bottom"><span className={g.kind === '完全相同' ? 'match-exact' : ''}>{g.kind}</span><span>{Math.round(g.confidence * 100)}% 相似</span></div>
              <div className="group-name" title={g.files[0].name}>{g.files[0].name}</div>
            </button>)}
            {!visibleGroups.length && <div className="groups-empty"><Layers2 size={27} strokeWidth={1.3}/><span>{query ? '没有匹配的分组' : state.scanning ? '正在查找重复文件' : '扫描后在此显示分组'}</span></div>}
          </div>
          <div className="sidebar-stats"><div><span>候选文件</span><strong>{allFiles.length}<small> 个</small></strong></div><div><span>已选清理</span><strong className={marks.size ? 'accent-text' : ''}>{fmt(markedSize)}</strong></div></div>
        </aside>

        <section className="review-panel">
          <div className="review-heading"><div><h2>{group ? `第 ${group.index + 1} 组` : '文件审阅'}{group && <span className="subtle-badge">{group.kind}</span>}</h2><p title={group?.reasons.join('；')}>{group ? <><CircleCheck size={12}/>{group.reasons.join(' · ')}</> : '扫描完成后，选择分组并对比文件画质'}</p></div></div>
          {group ? <>
            <div className="file-table-scroll">
              <table className="file-table"><thead><tr><th className="check-cell">清理</th><th>文件名</th><th>分辨率</th>{!photo && <th>时长 / 码率</th>}<th>大小</th><th>画质</th></tr></thead><tbody>
                {group.files.map(f => <tr key={f.id} tabIndex={0} aria-selected={f.id === current?.id} className={`${f.id === current?.id ? 'selected' : ''} ${marks.has(f.id) ? 'marked' : ''}`} onClick={() => setFileId(f.id)} onDoubleClick={() => api('open_file', f.id, false).catch(fail)} onKeyDown={e => { if (e.key === ' ') { e.preventDefault(); toggleMark(f.id); } if (e.key === 'Enter') setFileId(f.id); }}>
                  <td className="check-cell"><button className={`row-checkbox ${marks.has(f.id) ? 'checked' : ''}`} role="checkbox" aria-checked={marks.has(f.id)} aria-label={`清理 ${f.name}`} onClick={e => { e.stopPropagation(); toggleMark(f.id); }} onDoubleClick={e => e.stopPropagation()}>{marks.has(f.id) && <Check size={12}/>}</button></td>
                  <td><div className="file-name-cell"><span className="file-name" title={f.path}>{f.name}</span>{f.recommended && <span className="keep-badge"><Check size={10}/>建议保留</span>}<span className="file-format">{f.format}</span></div></td><td>{f.dimensions}</td>{!photo && <td>{f.stats}</td>}<td>{fmt(f.size)}</td><td><span className="quality-score">{Math.round(f.quality)}</span></td>
                </tr>)}
              </tbody></table>
            </div>
            <div className="preview-grid"><Preview file={winner} recommended onOpen={() => api('open_file', winner.id, false).catch(fail)}/><Preview file={current} onOpen={() => api('open_file', current.id, false).catch(fail)}/></div>
          </> : <div className="review-empty"><div className={`empty-illustration ${state.scanning ? 'scanning' : ''}`}><div className="empty-card back"><ImageIcon size={32} strokeWidth={1.2}/></div><div className="empty-card front">{state.scanning ? <ScanLine size={38} strokeWidth={1.2}/> : <Images size={38} strokeWidth={1.2}/>}</div><span className="empty-spark"><Check size={15}/></span></div><h3>{state.scanning ? '正在寻找相似的文件' : state.stage === '扫描完成' ? '没有发现重复文件' : '让每一份珍藏，都刚刚好'}</h3><p>{state.scanning ? '结果会在扫描结束后显示，你可以随时停止' : '选择照片或视频，扫描后并排对比，轻松决定保留哪一份'}</p><div className="empty-steps"><span><Folder size={14}/>选择文件夹</span><ChevronRight size={12}/><span><ScanLine size={14}/>扫描重复</span><ChevronRight size={12}/><span><CheckCheck size={14}/>审阅清理</span></div></div>}

          <footer className="action-bar"><div className="review-actions"><button className="button" disabled={!group || busy || state.scanning} title="保留本组推荐文件，勾选其余文件" onClick={() => recommend([group])}><CheckCheck size={15}/>本组建议</button><button className="button" disabled={!state.groups.length || busy || state.scanning} onClick={() => recommend(state.groups)}>全部建议</button><button className="icon-button" disabled={!marks.size || busy} title="清除勾选" aria-label="清除勾选" onClick={() => setMarks(new Set())}><RotateCcw size={15}/></button><span className="action-divider"/><button className="icon-button" disabled={!current || busy} title="打开所在文件夹" aria-label="打开所在文件夹" onClick={() => api('open_file', current.id, true).catch(fail)}><FolderOpen size={16}/></button></div><button className="button danger-button" disabled={!marks.size || busy || state.scanning} onClick={() => setDialog('recycle')}><Trash2 size={15}/>移入回收站{marks.size > 0 && <span className="delete-count">{marks.size}</span>}</button></footer>
        </section>
      </section>
    </main>

    <Dialog.Root open={!!dialog || about} onOpenChange={open => { if (!open && !busy) { setDialog(null); setAbout(false); } }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content" onEscapeKeyDown={e => busy && e.preventDefault()} onPointerDownOutside={e => busy && e.preventDefault()}>
      <Dialog.Title>{about ? '使用帮助' : dialog === 'recycle' ? '将选中的文件移入回收站？' : dialog === 'close' ? '停止扫描并退出？' : '未读取的文件'}</Dialog.Title>
      <Dialog.Description>{about ? '版本 1.0' : dialog === 'recycle' ? `已选 ${markedFiles.length} 个文件，共 ${fmt(markedSize)}。你可以从系统回收站恢复。` : dialog === 'close' ? '当前扫描将停止，路径和偏好设置会保存。' : '这些文件未参与比较，其余文件已正常处理。'}</Dialog.Description>
      {about ? <div className="about-body"><p><ShieldCheck size={16}/>照片和视频仅在本机分析，无需上传</p><p><Images size={16}/>支持常见照片、HEIC / HEIF、AVIF 与视频格式</p><p><Folder size={16}/>照片和视频各有一个基准文件夹，三个模式共用</p><p><CircleCheck size={16}/>推荐综合考虑分辨率、清晰度和压缩质量</p><p><CheckCheck size={16}/>点击清理列或按空格勾选，双击文件打开原图</p><p><Trash2 size={16}/>清理需要确认，文件可从系统回收站恢复</p></div> : dialog === 'recycle' ? <><div className="dialog-file-list">{markedFiles.slice(0,30).map(f => <div key={f.id}><FileImage size={14}/><span title={f.path}>{f.name}</span><small>{fmt(f.size)}</small></div>)}{markedFiles.length > 30 && <p>另有 {markedFiles.length - 30} 个文件</p>}</div>{state.groups.some(g => g.files.every(f => marks.has(f.id))) && <div className="warning-box"><AlertCircle size={16}/>至少有一个分组的所有文件都已被勾选，请确认无需保留。</div>}</> : dialog === 'errors' ? <div className="error-list">{state.errors.map((e,i) => <p key={i}>{e}</p>)}</div> : null}
      <div className="dialog-actions"><button className="button" disabled={busy} onClick={() => { setDialog(null); setAbout(false); }}>{about || dialog === 'errors' ? '知道了' : '取消'}</button>{dialog === 'recycle' && <button className="button danger-button" disabled={busy} onClick={recycle}>{busy ? <LoaderCircle className="spin" size={15}/> : <Trash2 size={15}/>}确认移入回收站</button>}{dialog === 'close' && <button className="button primary-button" onClick={() => windowAction('close')}>停止并退出</button>}</div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>
    <div className="resize-handle" onPointerDown={startResize}/>
    </div>;
}

function Preview({ file, recommended, onOpen }) {
  const [image, setImage] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let disposed = false; setImage(null); setError(false);
    if (file) api('get_preview', file.id).then(data => { if (!disposed) setImage(data); }).catch(() => { if (!disposed) setError(true); });
    return () => { disposed = true; };
  }, [file?.id]);
  return <div className="preview-panel"><div className="preview-heading"><span className={recommended ? 'keep-title' : ''}>{recommended ? <CircleCheck size={13}/> : <ImageIcon size={13}/>} {recommended ? '建议保留' : '当前文件'}</span><button className="icon-button" title="打开原文件" aria-label={`打开${recommended ? '建议保留' : '当前'}文件`} onClick={onOpen}><ExternalLink size={13}/></button></div><div className="preview-canvas" onDoubleClick={onOpen}>{image ? <img src={image} draggable={false} alt={file?.name}/> : error ? <span className="preview-loading"><AlertCircle size={22}/>无法预览，请双击打开原文件</span> : <LoaderCircle className="spin" size={20}/>}</div><div className="preview-meta" title={file?.path}><strong>{file?.name}</strong><span>{file?.dimensions}<span>·</span>{fmt(file?.size)}{file?.stats && <><span>·</span>{file.stats}</>}</span></div></div>;
}

createRoot(document.getElementById('root')).render(<App/>);
