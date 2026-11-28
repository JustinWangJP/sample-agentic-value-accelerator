/**
 * Registry ポータル 静的 HTML プロトタイプの共通シェル。
 * 本番 CSP（script-src 'self' / インラインスクリプト不可）に準拠し、
 * プロトタイプ確認用バー・上部ヘッダー（通知・主体）・サイドバー・モバイルメニュー・
 * 履歴ドロワー・表示状態の切替を prototype-model.js の定義から描画し、
 * すべての動作を data-* 属性のイベントリスナーで制御する。
 * 画面固有のデモ動作は prototype-pages.js に置く。
 */
(function () {
  'use strict';

  const model = window.RegistryPrototypeModel;
  if (!model) {
    throw new Error('prototype-model.js を prototype.js より前に読み込んでください。');
  }

  const ROLE_STORAGE_KEY = 'registry-portal-prototype-role';
  const VIEW_STATE_STORAGE_KEY = 'registry-portal-prototype-view-state';

  // ---- 汎用ヘルパー ----

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** 選択中かどうかで、2 組のクラスを切り替える（ボタン群・タブで共通） */
  function setActiveClasses(el, active, activeClasses, inactiveClasses) {
    activeClasses.forEach((cls) => el.classList.toggle(cls, active));
    inactiveClasses.forEach((cls) => el.classList.toggle(cls, !active));
  }

  function readStored(key, resolve) {
    try {
      return resolve(window.localStorage.getItem(key));
    } catch {
      // file:// 等で localStorage が使えない場合は既定値を使う
      return resolve(null);
    }
  }

  function writeStored(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // 保存できなくても表示は切り替える
    }
  }

  // ---- ナビゲーション ----

  const NAV_LINK_STYLES = {
    desktop: {
      current: 'flex items-center justify-between px-3 py-2 rounded-lg text-sm font-semibold bg-blue-50 text-blue-700',
      normal: 'flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-100',
      reference: 'flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100',
    },
    mobile: {
      current: 'block px-3 py-2 rounded-lg bg-blue-50 text-blue-700 font-semibold',
      normal: 'block px-3 py-2 rounded-lg text-slate-700 hover:bg-slate-100',
      reference: 'block px-3 py-2 rounded-lg text-slate-500 hover:bg-slate-100 border-t border-slate-200 pt-3',
    },
  };
  const NAV_SECTION_TITLE_CLASS =
    'px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400';

  function requiredRolesAttr(requiredRoles) {
    return requiredRoles.length > 0 ? ` data-requires-roles="${requiredRoles.join(' ')}"` : '';
  }

  /** ナビのリンク 1 件。デスクトップとモバイルで同じ組み立てを使い、クラスだけを切り替える */
  function buildNavLink(item, variant, activeNav, extraAttrs) {
    const styles = NAV_LINK_STYLES[variant];
    const isCurrent = item.key === activeNav;
    const currentAttr = isCurrent ? ' aria-current="page"' : '';
    const cls = isCurrent ? styles.current : styles.normal;
    const label = variant === 'desktop' ? `<span>${item.label}</span>` : item.label;
    return `<a href="${item.href}"${extraAttrs}${currentAttr} class="${cls}">${label}</a>`;
  }

  function buildStatesNavLink(variant) {
    const item = model.STATES_NAV_ITEM;
    const label = variant === 'desktop' ? `<span>${item.label}</span>` : item.label;
    return `<a href="${item.href}" class="${NAV_LINK_STYLES[variant].reference}">${label}</a>`;
  }

  function buildDesktopNavHtml(activeNav) {
    const sections = model.NAV_SECTIONS.map((section) => {
      const itemsHtml = section.items
        .map((item) => `<li>${buildNavLink(item, 'desktop', activeNav, '')}</li>`)
        .join('');
      return `<div${requiredRolesAttr(section.requiredRoles)}><div class="${NAV_SECTION_TITLE_CLASS}">${section.title}</div><ul class="space-y-1">${itemsHtml}</ul></div>`;
    }).join('');
    const reference = `<div class="pt-4 border-t border-slate-200"><div class="${NAV_SECTION_TITLE_CLASS}">${model.STATES_NAV_ITEM.sectionTitle}</div><ul class="space-y-1"><li>${buildStatesNavLink('desktop')}</li></ul></div>`;
    return sections + reference;
  }

  function buildMobileNavHtml(activeNav) {
    const links = model.NAV_SECTIONS.flatMap((section) =>
      section.items.map((item) =>
        buildNavLink(item, 'mobile', activeNav, requiredRolesAttr(section.requiredRoles)),
      ),
    ).join('');
    return links + buildStatesNavLink('mobile');
  }

  // ---- プロトタイプ確認用バー ----

  const TOGGLE_BUTTON_ACTIVE = ['bg-slate-900', 'text-white'];
  const TOGGLE_BUTTON_INACTIVE = ['bg-white', 'text-slate-700'];

  /** 業務ロール切替・表示状態切替で共通のボタン群 */
  function buildToggleButtons(entries, dataAttr) {
    return entries
      .map(
        ([key, label]) =>
          `<button type="button" ${dataAttr}="${key}" class="px-2 py-0.5 rounded border border-slate-700 text-xs font-medium cursor-pointer transition">${label}</button>`,
      )
      .join('');
  }

  function buildPrototypeBarHtml(statesHref, statesLabel) {
    const roleButtons = buildToggleButtons(
      Object.entries(model.ROLE_PRESETS).map(([key, preset]) => [key, preset.label]),
      'data-role-preset',
    );
    const viewStateButtons = buildToggleButtons(
      model.VIEW_STATES.map((state) => [state.key, state.label]),
      'data-view-state',
    );
    return `
  <!-- プロトタイプ確認用バー（本番 SPA には含めない） -->
  <div class="bg-slate-900 text-slate-200 text-xs px-4 py-2 border-b border-slate-800">
    <div class="max-w-screen-2xl mx-auto flex flex-wrap items-center justify-between gap-2">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span class="inline-flex items-center px-2 py-0.5 rounded bg-blue-500/20 text-blue-200 font-semibold tracking-wide">PROTOTYPE</span>
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-slate-400">業務ロール切替:</span>
          <div class="inline-flex flex-wrap gap-1" role="group" aria-label="プロトタイプ業務ロール切替">${roleButtons}</div>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-slate-400">表示状態:</span>
          <div class="inline-flex flex-wrap gap-1" role="group" aria-label="プロトタイプ表示状態切替">${viewStateButtons}</div>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <a href="${statesHref}" class="text-blue-300 hover:text-blue-200 underline underline-offset-2">${statesLabel}</a>
      </div>
    </div>
  </div>`;
  }

  // ---- 上部ヘッダー（通知・主体） ----

  const NOTIFICATION_KIND_CLASSES = {
    SUBMITTED: 'bg-amber-100 text-amber-800',
    APPROVED: 'bg-emerald-100 text-emerald-800',
    OUTSIDE_CHANGE: 'bg-rose-100 text-rose-800',
  };

  function buildNotificationItemHtml(item) {
    const rowAttr = item.unread ? ' data-unread-row' : '';
    const rowClass = item.unread ? 'px-4 py-3 bg-blue-50/50' : 'px-4 py-3';
    const dot = item.unread
      ? '<span data-unread-dot class="mt-1.5 w-2 h-2 rounded-full bg-blue-600 shrink-0"></span>'
      : '<span class="mt-1.5 w-2 h-2 rounded-full bg-transparent shrink-0"></span>';
    return `
              <li${rowAttr} class="${rowClass}">
                <div class="flex items-start gap-2.5">
                  ${dot}
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-[11px] font-semibold px-1.5 py-0.5 rounded ${NOTIFICATION_KIND_CLASSES[item.kind]}">${escapeHtml(model.NOTIFICATION_KIND_LABELS[item.kind])}</span>
                      <span class="text-xs text-slate-500">${escapeHtml(item.at)}</span>
                    </div>
                    <p class="text-sm font-medium text-slate-900 mt-1">${escapeHtml(item.title)}</p>
                    <p class="text-xs text-slate-600 mt-0.5">${escapeHtml(item.body)}</p>
                  </div>
                </div>
              </li>`;
  }

  function buildHeaderHtml() {
    const unreadCount = model.NOTIFICATION_SAMPLES.filter((item) => item.unread).length;
    const notificationsHtml = model.NOTIFICATION_SAMPLES.map(buildNotificationItemHtml).join('');
    return `
  <!-- 上部ヘッダー -->
  <header class="sticky top-0 z-30 bg-white border-b border-slate-200 h-15 flex items-center px-4 lg:px-6">
    <div class="w-full max-w-screen-2xl mx-auto flex items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <button type="button" data-open-drawer="mobile-nav-drawer" class="lg:hidden inline-flex items-center justify-center p-2 rounded-lg text-slate-600 hover:bg-slate-100 cursor-pointer" aria-label="メニューを開く">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16"/></svg>
        </button>
        <a href="./index.html" class="flex items-center gap-2.5 text-slate-900 no-underline">
          <span class="w-8 h-8 rounded-lg bg-blue-600 text-white font-bold text-sm inline-flex items-center justify-center shadow-xs">R</span>
          <div class="leading-tight">
            <div class="font-semibold text-sm tracking-tight">Registry ポータル</div>
            <div class="text-[11px] text-slate-500">AWS Agent Registry 管理・発見基盤</div>
          </div>
        </a>
      </div>

      <div class="flex items-center gap-2">
        <!-- 通知ポップオーバー -->
        <div class="relative">
          <button type="button" data-toggle-popover="notifications-popover" aria-expanded="false" aria-controls="notifications-popover" class="relative inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-100 border border-slate-200 cursor-pointer">
            <svg class="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0a3 3 0 1 1-6 0"/></svg>
            <span class="hidden sm:inline">通知</span>
            <span data-unread-badge class="inline-flex items-center justify-center min-w-5 h-5 px-1.5 text-xs font-semibold rounded-full bg-blue-600 text-white">${unreadCount}</span>
          </button>

          <div id="notifications-popover" data-popover class="is-hidden absolute right-0 mt-2 w-96 max-w-[calc(100vw-2rem)] bg-white rounded-xl border border-slate-200 shadow-xl z-40 overflow-hidden">
            <div class="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
              <div>
                <h2 class="text-sm font-semibold text-slate-900">通知</h2>
                <p class="text-xs text-slate-500">約60秒ごとに自動更新（90日間保持）</p>
              </div>
              <button type="button" data-mark-all-read class="text-xs font-medium text-blue-600 hover:text-blue-700 cursor-pointer">すべて既読にする</button>
            </div>
            <div data-state-region data-state-label="通知" data-state-error="502 NOTIFICATIONS_UNAVAILABLE">
              <ul class="divide-y divide-slate-100 max-h-80 overflow-y-auto">${notificationsHtml}
              </ul>
            </div>
            <div class="px-4 py-2.5 bg-slate-50 border-t border-slate-200 text-xs text-slate-500 flex items-center justify-between">
              <span>最大 50 件を表示</span>
              <a href="./states.html#notifications-states" class="text-blue-600 hover:underline">取得失敗時の表示例</a>
            </div>
          </div>
        </div>

        <!-- 主体・業務ロール・所属チーム ポップオーバー -->
        <div class="relative">
          <button type="button" data-toggle-popover="principal-popover" aria-expanded="false" aria-controls="principal-popover" class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-100 border border-slate-200 cursor-pointer">
            <span class="w-6 h-6 rounded-full bg-slate-800 text-white text-xs font-semibold inline-flex items-center justify-center">主</span>
            <span class="hidden md:inline max-w-44 truncate text-xs font-medium" data-role-summary></span>
            <span class="inline-flex items-center justify-center px-1.5 py-0.5 text-[11px] font-semibold rounded bg-slate-100 text-slate-700" data-role-count></span>
          </button>

          <div id="principal-popover" data-popover class="is-hidden absolute right-0 mt-2 w-96 max-w-[calc(100vw-2rem)] bg-white rounded-xl border border-slate-200 shadow-xl z-40 p-4">
            <h2 class="text-sm font-semibold text-slate-900 mb-3">自分の主体・業務ロール・所属チーム</h2>
            <dl class="space-y-2.5 text-xs">
              <div>
                <dt class="text-slate-500 font-medium">発行元（iss）</dt>
                <dd class="mt-0.5 font-mono text-slate-800 break-all bg-slate-50 px-2 py-1 rounded border border-slate-200">https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbCdEf123</dd>
              </div>
              <div>
                <dt class="text-slate-500 font-medium">主体（sub）</dt>
                <dd class="mt-0.5 font-mono text-slate-800 break-all bg-slate-50 px-2 py-1 rounded border border-slate-200">74a8b1c2-9d0e-4f1a-8b2c-3d4e5f6a7b8c</dd>
              </div>
              <div>
                <dt class="text-slate-500 font-medium">業務ロール</dt>
                <dd class="mt-0.5 text-slate-900 font-medium" data-role-summary></dd>
              </div>
              <div>
                <dt class="text-slate-500 font-medium">所属チーム</dt>
                <dd class="mt-0.5 text-slate-900" data-team-summary></dd>
              </div>
            </dl>
            <p class="mt-3 pt-3 border-t border-slate-200 text-[11px] text-slate-500 leading-relaxed">
              アクセストークンはメモリー上だけで保持しています。ページを再読み込みすると、もう一度 SSO でログインします。
            </p>
          </div>
        </div>
      </div>
    </div>
  </header>`;
  }

  function mountPortalShell() {
    const body = document.body;
    const activeNav = body.getAttribute('data-portal-nav');
    if (!activeNav) return;

    const statesHref = body.getAttribute('data-states-href') || model.STATES_NAV_ITEM.href;
    const statesLabel =
      body.getAttribute('data-states-label') || '状態・エラー・デザインシステム一覧 →';

    const topSlot = document.querySelector('[data-portal-shell-top]');
    if (topSlot) {
      topSlot.innerHTML = buildPrototypeBarHtml(statesHref, statesLabel) + buildHeaderHtml();
    }

    const sidebarSlot = document.querySelector('[data-portal-shell-sidebar]');
    if (sidebarSlot) {
      sidebarSlot.innerHTML = `<nav aria-label="メインナビゲーション" class="space-y-6">${buildDesktopNavHtml(activeNav)}</nav>`;
    }

    const mobileDrawer = document.createElement('div');
    mobileDrawer.id = 'mobile-nav-drawer';
    mobileDrawer.setAttribute('data-drawer', '');
    mobileDrawer.setAttribute('aria-hidden', 'true');
    mobileDrawer.className = 'is-hidden fixed inset-0 z-50 flex';
    mobileDrawer.innerHTML = `
    <div data-close-drawer class="fixed inset-0 bg-slate-900/40"></div>
    <div class="relative w-72 max-w-[85vw] bg-white h-full shadow-2xl p-4 flex flex-col">
      <div class="flex items-center justify-between pb-3 border-b border-slate-200">
        <span class="font-semibold text-sm text-slate-900">メニュー</span>
        <button type="button" data-close-drawer class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 cursor-pointer" aria-label="閉じる">✕</button>
      </div>
      <nav aria-label="モバイルナビゲーション" class="mt-4 space-y-2 text-sm overflow-y-auto">${buildMobileNavHtml(activeNav)}</nav>
    </div>`;
    body.appendChild(mobileDrawer);
  }

  // ---- 操作と審査の履歴ドロワー（右スライドオーバー） ----

  const HISTORY_KIND_CLASSES = {
    APPROVE: { dot: 'bg-emerald-500', label: 'text-emerald-700' },
    SUBMIT: { dot: 'bg-amber-500', label: 'text-amber-800' },
    UPDATE: { dot: 'bg-indigo-500', label: 'text-indigo-700' },
    REQUEST_DEPRECATION: { dot: 'bg-rose-500', label: 'text-rose-800' },
  };

  function buildHistoryEntryHtml(entry) {
    const classes = HISTORY_KIND_CLASSES[entry.kind];
    const kindLabel = model.HISTORY_KIND_LABELS[entry.kind];
    const stateLabel = model.HISTORY_STATE_LABELS[entry.state];
    const reason = entry.reason
      ? `<p class="mt-1 text-xs text-slate-700">理由: ${escapeHtml(entry.reason)}</p>`
      : '';
    const details = entry.details
      ? `<dl class="mt-2 text-[11px] text-slate-500 font-mono space-y-0.5 bg-slate-50 p-2.5 rounded border border-slate-200">${entry.details
          .map((line) => `<div>${escapeHtml(line)}</div>`)
          .join('')}</dl>`
      : '';
    const recovery = entry.recovery
      ? `<div class="mt-1.5 text-xs bg-indigo-50 border border-indigo-200 rounded p-2.5 text-indigo-900"><div class="font-semibold">${escapeHtml(entry.recovery.title)}</div>${entry.recovery.lines
          .map((line) => `<div>${escapeHtml(line)}</div>`)
          .join('')}</div>`
      : '';
    return `
          <li class="pl-5 relative">
            <span class="absolute -left-1.5 top-1.5 w-3 h-3 rounded-full ${classes.dot} ring-4 ring-white"></span>
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-semibold ${classes.label}">${escapeHtml(kindLabel)}（${escapeHtml(stateLabel)} / ${entry.state}）</span>
              <span class="text-[11px] text-slate-500">${escapeHtml(entry.at)}</span>
            </div>${reason}${details}${recovery}
          </li>`;
  }

  /** data-history-drawer="<サンプル名>" の置き場所を、共通の履歴ドロワーに置き換える */
  function mountHistoryDrawer() {
    const slot = document.querySelector('[data-history-drawer]');
    if (!slot) return;
    const sample = model.HISTORY_SAMPLES[slot.getAttribute('data-history-drawer') || ''];
    if (!sample) {
      console.warn('[prototype] 未定義の履歴サンプルです:', slot.getAttribute('data-history-drawer'));
      return;
    }
    const drawer = document.createElement('div');
    drawer.id = 'history-drawer';
    drawer.setAttribute('data-drawer', '');
    drawer.setAttribute('aria-hidden', 'true');
    drawer.className = 'is-hidden fixed inset-0 z-50 flex justify-end';
    drawer.innerHTML = `
    <div data-close-drawer class="fixed inset-0 bg-slate-900/40"></div>
    <div role="dialog" aria-modal="true" aria-labelledby="history-drawer-title" class="relative w-full max-w-lg bg-white h-full shadow-2xl flex flex-col">
      <div class="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
        <div>
          <h2 id="history-drawer-title" class="text-base font-semibold text-slate-900">操作と審査の履歴</h2>
          <p class="text-xs font-mono text-slate-500">${escapeHtml(sample.subtitle)}</p>
        </div>
        <button type="button" data-close-drawer class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 cursor-pointer" aria-label="閉じる">✕</button>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <ol class="relative border-l border-slate-200 ml-2 space-y-5">${sample.entries.map(buildHistoryEntryHtml).join('')}
        </ol>
      </div>
    </div>`;
    slot.replaceWith(drawer);
  }

  // ---- select の選択肢（prototype-model.js の単一定義から描画） ----

  function populateOptionSets() {
    document.querySelectorAll('select[data-options]').forEach((select) => {
      const setName = select.getAttribute('data-options') || '';
      const options = model.OPTION_SETS[setName];
      if (!options) {
        console.warn('[prototype] 未定義の選択肢です:', setName);
        return;
      }
      const selected = select.getAttribute('data-selected') || '';
      const emptyLabel = select.getAttribute('data-empty-label');
      const emptyOption =
        emptyLabel !== null ? `<option value="">${escapeHtml(emptyLabel)}</option>` : '';
      select.innerHTML =
        emptyOption +
        options
          .map(
            (option) =>
              `<option value="${escapeHtml(option.value)}"${option.value === selected ? ' selected' : ''}>${escapeHtml(option.label)}</option>`,
          )
          .join('');
    });
  }

  // ---- 業務ロール切替 ----

  function applyRolePreset(presetKey) {
    const key = model.resolveRolePreset(presetKey);
    const preset = model.ROLE_PRESETS[key];
    writeStored(ROLE_STORAGE_KEY, key);

    document.querySelectorAll('[data-role-preset]').forEach((btn) => {
      const active = btn.getAttribute('data-role-preset') === key;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      setActiveClasses(btn, active, TOGGLE_BUTTON_ACTIVE, TOGGLE_BUTTON_INACTIVE);
    });

    document.querySelectorAll('[data-requires-roles]').forEach((el) => {
      const required = model.parseTokenList(el.getAttribute('data-requires-roles'));
      el.classList.toggle('is-hidden', !model.isAllowedForRoles(required, preset.roles));
    });

    document.querySelectorAll('[data-role-summary]').forEach((el) => {
      el.textContent = preset.roles.join(' / ');
    });
    document.querySelectorAll('[data-role-count]').forEach((el) => {
      el.textContent = String(preset.roles.length);
    });
    document.querySelectorAll('[data-team-summary]').forEach((el) => {
      el.textContent = preset.teams.length > 0 ? preset.teams.join('、') : '所属チームなし';
    });
  }

  /** HTML の data-requires-roles に ROLES 外の値がないかを確認する（指定ミスの早期発見） */
  function warnUnknownRoles() {
    document.querySelectorAll('[data-requires-roles]').forEach((el) => {
      const required = model.parseTokenList(el.getAttribute('data-requires-roles'));
      const unknown = model.findUnknownRoles(required);
      if (unknown.length > 0) {
        console.warn('[prototype] 未定義の業務ロールが指定されています:', unknown, el);
      }
    });
  }

  function initRoleBar() {
    warnUnknownRoles();
    document.querySelectorAll('[data-role-preset]').forEach((btn) => {
      btn.addEventListener('click', () => applyRolePreset(btn.getAttribute('data-role-preset') || ''));
    });
    applyRolePreset(readStored(ROLE_STORAGE_KEY, model.resolveRolePreset));
  }

  // ---- 表示状態（0 件・未取得・準備中・失敗）の切替 ----

  const PANEL_FRAME = 'rounded-xl p-5 space-y-3';
  const BADGE = 'inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold';
  const REQUEST_ID_SAMPLE = 'requestId: 8f14e45f-ceea-467a-9575-2b0a1e7c8d33';

  /**
   * 表示状態ごとのパネル。一覧領域（data-state-region）と states.html の表示例（data-state-sample）で共通。
   * 401 だけが再ログイン、5xx だけが再試行を出し、403 は再操作しても解決しないためどちらも出さない。
   */
  const STATE_PANEL_BUILDERS = {
    loading: ({ label }) => `
      <div role="status" class="bg-white border border-slate-200 ${PANEL_FRAME} flex flex-col items-center justify-center text-center">
        <div class="w-7 h-7 rounded-full border-2 border-slate-200 border-t-blue-600 animate-spin"></div>
        <p class="text-sm font-medium text-slate-700">${label}を読み込んでいます…</p>
      </div>`,
    empty: ({ label, emptyHint }) => `
      <div class="bg-white border border-slate-200 ${PANEL_FRAME} text-center">
        <span class="${BADGE} bg-slate-100 text-slate-600">0 件（正常応答）</span>
        <h3 class="text-sm font-semibold text-slate-900">${label}はありません</h3>
        <p class="text-xs text-slate-500">${emptyHint || '条件を変えて再度確認してください。'}</p>
      </div>`,
    preparing: () => `
      <div class="bg-amber-50 border border-amber-200 ${PANEL_FRAME}">
        <span class="${BADGE} bg-amber-100 text-amber-900">準備中（503 A2A_SEARCH_NOT_READY）</span>
        <h3 class="text-sm font-semibold text-amber-950">A2A での絞り込み検索は現在準備中です</h3>
        <p class="text-xs text-amber-900">A2A の Agent を探す場合は、一覧画面の「A2A の Agent のみ表示」をご利用ください。準備ができるまでは再試行しても解決しないため、再試行ボタンは表示しません。</p>
        <a href="./index.html" class="inline-flex items-center px-3.5 py-1.5 rounded-lg bg-white border border-amber-300 hover:bg-amber-100 text-amber-900 text-xs font-semibold">A2A の Agent 一覧を開く →</a>
      </div>`,
    error401: () => `
      <div role="alert" class="bg-rose-50 border border-rose-200 ${PANEL_FRAME}">
        <span class="${BADGE} font-mono bg-rose-100 text-rose-800">失敗（401 UNAUTHENTICATED）</span>
        <h3 class="text-sm font-semibold text-rose-950">認証が必要です。ログインし直してください。</h3>
        <p class="text-xs text-rose-700">401 またはログイン失敗のときだけ、再ログインボタンを表示します。</p>
        <button type="button" class="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold cursor-pointer">もう一度ログインする</button>
      </div>`,
    error403: ({ label }) => `
      <div role="alert" class="bg-rose-50 border border-rose-200 ${PANEL_FRAME}">
        <span class="${BADGE} font-mono bg-rose-100 text-rose-800">失敗（403 FORBIDDEN）</span>
        <h3 class="text-sm font-semibold text-rose-950">${label}を表示できません</h3>
        <p class="text-xs text-rose-800">必要な業務ロールまたは所属チームがありません。ログインし直しても、再試行しても解決しないため、再ログイン・再試行のボタンは表示しません。管理者に業務ロールと所属チームを確認してください。</p>
        <p class="text-xs font-mono text-rose-700">${REQUEST_ID_SAMPLE}</p>
      </div>`,
    error5xx: ({ label, errorCode }) => `
      <div role="alert" class="bg-rose-50 border border-rose-200 ${PANEL_FRAME}">
        <span class="${BADGE} font-mono bg-rose-100 text-rose-800">失敗（${errorCode || '5xx'}）</span>
        <h3 class="text-sm font-semibold text-rose-950">${label}を取得できませんでした</h3>
        <p class="text-xs text-rose-800">一時的な通信異常またはサービス障害のため、結果を表示できません（0 件ではありません）。</p>
        <p class="text-xs font-mono text-rose-700">${REQUEST_ID_SAMPLE}</p>
        <button type="button" class="px-3.5 py-1.5 rounded-lg border border-rose-300 bg-white hover:bg-rose-100 text-rose-800 text-xs font-semibold cursor-pointer">再試行する</button>
      </div>`,
  };

  function buildStatePanelHtml(viewState, el) {
    const build = STATE_PANEL_BUILDERS[viewState];
    if (!build) return '';
    return build({
      label: escapeHtml(el.getAttribute('data-state-label') || 'データ'),
      errorCode: escapeHtml(el.getAttribute('data-state-error') || ''),
      emptyHint: escapeHtml(el.getAttribute('data-state-empty-hint') || ''),
    });
  }

  /** 一覧領域の直後に、表示状態のパネルを置く場所を用意する */
  const regionPanels = new WeakMap();
  function getRegionPanel(region) {
    let panel = regionPanels.get(region);
    if (!panel) {
      panel = document.createElement('div');
      panel.setAttribute('data-state-panel', '');
      panel.className = 'is-hidden p-4';
      region.insertAdjacentElement('afterend', panel);
      regionPanels.set(region, panel);
    }
    return panel;
  }

  function applyViewState(stateKey) {
    const viewState = model.resolveViewState(stateKey);
    writeStored(VIEW_STATE_STORAGE_KEY, viewState);

    document.querySelectorAll('[data-view-state]').forEach((btn) => {
      const active = btn.getAttribute('data-view-state') === viewState;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      setActiveClasses(btn, active, TOGGLE_BUTTON_ACTIVE, TOGGLE_BUTTON_INACTIVE);
    });

    document.querySelectorAll('[data-state-region]').forEach((region) => {
      const kinds = model.parseTokenList(region.getAttribute('data-state-kinds'));
      const replaced =
        viewState !== model.DEFAULT_VIEW_STATE && model.regionShowsState(kinds, viewState);
      const panel = getRegionPanel(region);
      region.classList.toggle('is-hidden', replaced);
      panel.classList.toggle('is-hidden', !replaced);
      panel.innerHTML = replaced ? buildStatePanelHtml(viewState, region) : '';
    });

    document.querySelectorAll('[data-state-only]').forEach((el) => {
      const kinds = model.parseTokenList(el.getAttribute('data-state-only'));
      el.classList.toggle('is-hidden', !kinds.includes(viewState));
    });
    document.querySelectorAll('[data-state-hide]').forEach((el) => {
      const kinds = model.parseTokenList(el.getAttribute('data-state-hide'));
      el.classList.toggle('is-hidden', kinds.includes(viewState));
    });
  }

  function initViewStateBar() {
    document.querySelectorAll('[data-view-state]').forEach((btn) => {
      btn.addEventListener('click', () => applyViewState(btn.getAttribute('data-view-state') || ''));
    });
    applyViewState(readStored(VIEW_STATE_STORAGE_KEY, model.resolveViewState));
  }

  /** states.html の表示例（data-state-sample）を、一覧領域と同じパネルで描画する */
  function renderStateSamples() {
    document.querySelectorAll('[data-state-sample]').forEach((el) => {
      el.innerHTML = buildStatePanelHtml(el.getAttribute('data-state-sample') || '', el);
    });
  }

  // ---- ポップオーバー・ドロワー・モーダル ----

  function closeAllPopovers(exceptId) {
    document.querySelectorAll('[data-popover]').forEach((panel) => {
      if (panel.id !== exceptId) {
        panel.classList.add('is-hidden');
      }
    });
    document.querySelectorAll('[data-toggle-popover]').forEach((trigger) => {
      const targetId = trigger.getAttribute('data-toggle-popover');
      if (targetId !== exceptId) {
        trigger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  function initPopovers() {
    document.querySelectorAll('[data-toggle-popover]').forEach((trigger) => {
      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const targetId = trigger.getAttribute('data-toggle-popover');
        const panel = targetId ? document.getElementById(targetId) : null;
        if (!panel) return;
        const willOpen = panel.classList.contains('is-hidden');
        closeAllPopovers(willOpen ? targetId : null);
        panel.classList.toggle('is-hidden', !willOpen);
        trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
      });
    });

    document.addEventListener('click', (e) => {
      const target = e.target;
      if (!(target instanceof Element)) return;
      if (!target.closest('[data-popover]') && !target.closest('[data-toggle-popover]')) {
        closeAllPopovers(null);
      }
    });
  }

  // ドロワー・モーダル共通の開閉ヘルパー
  function setOverlayOpen(overlay, isOpen) {
    if (!overlay) return;
    overlay.classList.toggle('is-hidden', !isOpen);
    overlay.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
  }

  function bindOverlayKind(kind) {
    const openAttr = `data-open-${kind}`;
    const closeAttr = `data-close-${kind}`;
    const containerSelector = `[data-${kind}]`;

    document.querySelectorAll(`[${openAttr}]`).forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute(openAttr);
        if (id) setOverlayOpen(document.getElementById(id), true);
      });
    });

    document.querySelectorAll(`[${closeAttr}]`).forEach((btn) => {
      btn.addEventListener('click', () => {
        setOverlayOpen(btn.closest(containerSelector), false);
      });
    });
  }

  function initDrawersAndModals() {
    ['drawer', 'modal'].forEach(bindOverlayKind);

    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      closeAllPopovers(null);
      document
        .querySelectorAll('[data-drawer]:not(.is-hidden), [data-modal]:not(.is-hidden)')
        .forEach((overlay) => setOverlayOpen(overlay, false));
    });
  }

  // ---- タブ ----

  const TAB_ACTIVE = ['border-blue-600', 'text-blue-700', 'bg-blue-50/70'];
  const TAB_INACTIVE = ['border-transparent', 'text-slate-600'];

  function activateTab(group, btn) {
    const groupName = group.getAttribute('data-tab-group');
    const targetId = btn.getAttribute('data-tab-target');
    group.querySelectorAll('[data-tab-target]').forEach((b) => {
      const active = b === btn;
      b.setAttribute('aria-selected', active ? 'true' : 'false');
      setActiveClasses(b, active, TAB_ACTIVE, TAB_INACTIVE);
    });
    document.querySelectorAll(`[data-tab-panel-group="${groupName}"]`).forEach((panel) => {
      panel.classList.toggle('is-hidden', panel.id !== targetId);
    });
  }

  function syncTabsFromHash() {
    const hash = window.location.hash.replace(/^#/, '');
    if (!hash) return;
    document.querySelectorAll('[data-tab-group]').forEach((group) => {
      const matched = group.querySelector(
        `[data-tab-hash="${hash}"], [data-tab-target="${hash}"]`,
      );
      if (matched) activateTab(group, matched);
    });
  }

  function initTabs() {
    document.querySelectorAll('[data-tab-group]').forEach((group) => {
      group.querySelectorAll('[data-tab-target]').forEach((btn) => {
        btn.addEventListener('click', () => activateTab(group, btn));
      });
    });
    syncTabsFromHash();
    window.addEventListener('hashchange', syncTabsFromHash);
  }

  // ---- 通知の既読化 ----

  function initNotificationsReadMarker() {
    document.querySelectorAll('[data-mark-all-read]').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('[data-unread-badge], [data-unread-dot]').forEach((el) => {
          el.classList.add('is-hidden');
        });
        document.querySelectorAll('[data-unread-row]').forEach((row) => {
          row.classList.remove('bg-blue-50/50');
        });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    // 描画（以降のイベント登録より先に、すべての要素を DOM に置く）
    mountPortalShell();
    mountHistoryDrawer();
    populateOptionSets();
    renderStateSamples();
    // 動作
    initRoleBar();
    initViewStateBar();
    initPopovers();
    initDrawersAndModals();
    initTabs();
    initNotificationsReadMarker();
  });
})();
