(() => {
  const button = document.getElementById('theme-toggle');
  const label = document.getElementById('theme-label');
  const poster = document.getElementById('poster');
  const storageKey = 'daily-brief-preview-theme';
  const video = document.getElementById('brief-video');
  const videoDetail = document.getElementById('video-detail');
  const videoRefresh = document.getElementById('video-refresh');
  const posterLabel = document.getElementById('poster-label');
  let manifest = null;
  let switchToken = 0;
  let requestToken = 0;

  function showPlaceholder(message) {
    switchToken += 1;
    video.pause();
    if (video.dataset.source) {
      video.removeAttribute('src');
      delete video.dataset.source;
      video.load();
    }
    video.hidden = true;
    poster.hidden = false;
    posterLabel.hidden = false;
    videoDetail.textContent = message;
  }

  function updateVideo() {
    if (!manifest) return;
    const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    const other = theme === 'dark' ? 'light' : 'dark';
    const chosen = manifest.videos?.[theme]?.available ? theme : (manifest.videos?.[other]?.available ? other : null);
    if (!chosen || !manifest.videos[chosen].url) {
      showPlaceholder('아직 영상이 없어요. 영상을 만든 뒤 ‘영상 확인’을 눌러 주세요.');
      return;
    }
    const source = manifest.videos[chosen].url;
    const fallback = chosen !== theme ? ` · ${chosen === 'light' ? '라이트' : '다크'} 영상 표시 중` : '';
    const demo = manifest.demo ? '테스트 영상' : '생성된 영상';
    videoDetail.textContent = demo + fallback;
    if (video.dataset.source === source) return;

    const position = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const resume = Boolean(video.dataset.source) && !video.paused;
    const token = ++switchToken;
    video.pause();
    video.hidden = false;
    poster.hidden = true;
    posterLabel.hidden = true;
    video.dataset.source = source;
    video.addEventListener('loadedmetadata', () => {
      if (token !== switchToken) return;
      if (position > 0 && Number.isFinite(video.duration)) {
        try { video.currentTime = Math.min(position, Math.max(0, video.duration - 0.05)); } catch (_) { /* seek may be unavailable */ }
      }
      if (resume) video.play().catch(() => { videoDetail.textContent = `${demo}${fallback} · 재생 버튼을 눌러 주세요`; });
    }, { once: true });
    video.src = source;
    video.load();
  }

  async function refreshVideos() {
    const token = ++requestToken;
    videoRefresh.disabled = true;
    try {
      const response = await fetch('/api/videos', { cache: 'no-store' });
      if (!response.ok) throw new Error('video list unavailable');
      const result = await response.json();
      if (!result.videos) throw new Error('invalid video list');
      if (token !== requestToken) return;
      manifest = result;
      updateVideo();
    } catch (_) {
      if (token !== requestToken) return;
      manifest = null;
      showPlaceholder('영상 서버에 연결할 수 없어요. 웹 서버를 실행해 주세요.');
    } finally {
      if (token === requestToken) videoRefresh.disabled = false;
    }
  }
  video.addEventListener('error', () => {
    if (!video.hidden && video.getAttribute('src')) videoDetail.textContent = '영상을 재생할 수 없어요. 영상 파일을 확인해 주세요.';
  });
  videoRefresh.addEventListener('click', refreshVideos);

  function setTheme(theme) {
    const dark = theme === 'dark';
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    button.setAttribute('aria-pressed', String(dark));
    button.setAttribute('aria-label', dark ? '라이트 모드로 전환' : '다크 모드로 전환');
    label.textContent = dark ? '라이트 모드' : '다크 모드';
    poster.src = dark ? './assets/dark_navy_sample.png' : './assets/minimal_white_sample.png';
    try { localStorage.setItem(storageKey, dark ? 'dark' : 'light'); } catch (_) { /* works without storage */ }
    updateVideo();
  }

  let initialTheme = 'light';
  try { initialTheme = localStorage.getItem(storageKey) === 'dark' ? 'dark' : 'light'; } catch (_) { /* use light */ }
  setTheme(initialTheme);
  button.addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  refreshVideos();

  // Tags are only saved in the current browser. They do not call a news API.
  const form = document.getElementById('tag-form');
  const input = document.getElementById('tag-input');
  const list = document.getElementById('tag-list');
  const empty = document.getElementById('tag-empty');
  const count = document.getElementById('tag-count');
  const status = document.getElementById('tag-status');
  const tagStorageKey = 'daily-brief-preview-tags';
  const keyFor = value => value.normalize('NFKC').replace(/\s+/g, ' ').trim().toLocaleLowerCase('ko-KR');
  let tags = [];
  try {
    const saved = JSON.parse(localStorage.getItem(tagStorageKey) || '[]');
    if (Array.isArray(saved)) tags = saved.filter(value => typeof value === 'string' && value.trim()).slice(0, 20);
  } catch (_) { /* keep an empty in-memory list */ }

  function saveTags() {
    try {
      localStorage.setItem(tagStorageKey, JSON.stringify(tags));
      return true;
    } catch (_) { return false; }
  }

  function renderTags() {
    list.replaceChildren();
    empty.hidden = tags.length > 0;
    count.textContent = `${tags.length} / 20`;
    tags.forEach((tag, index) => {
      const item = document.createElement('li');
      item.className = 'tag-chip';
      const name = document.createElement('span');
      name.textContent = `# ${tag}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `${tag} 태그 삭제`);
      remove.addEventListener('click', () => {
        tags.splice(index, 1);
        const stored = saveTags();
        renderTags();
        status.textContent = stored ? `‘${tag}’ 태그를 삭제했어요.` : '브라우저 저장이 차단되어 이번 화면에서만 삭제됐어요.';
      });
      item.append(name, remove);
      list.append(item);
    });
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    const value = input.value.replace(/\s+/g, ' ').trim();
    if (!value) { status.textContent = '등록할 키워드나 티커를 입력해 주세요.'; input.focus(); return; }
    if (tags.length >= 20) { status.textContent = '태그는 최대 20개까지 등록할 수 있어요.'; return; }
    if (tags.some(tag => keyFor(tag) === keyFor(value))) {
      status.textContent = '이미 등록된 태그예요.';
      input.focus();
      return;
    }
    tags.push(value);
    const stored = saveTags();
    renderTags();
    status.textContent = stored ? `‘${value}’ 태그를 등록했어요.` : '브라우저 저장이 차단되어 이번 화면에서만 표시돼요.';
    input.value = '';
    input.focus();
  });
  renderTags();
})();
