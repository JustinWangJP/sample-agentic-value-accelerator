/**
 * Registry ポータル 静的 HTML プロトタイプ用の共通シェルとインタラクション。
 * 本番 CSP（script-src 'self' / インラインスクリプト不可）に準拠し、
 * 共通ヘッダー・サイドバー・モバイルメニューを単一の定義から描画し、
 * すべての動作を data-* 属性のイベントリスナーで制御する。
 */
(function () {
  'use strict';

  const ROLE_STORAGE_KEY = 'registry-portal-prototype-role';

  const ROLE_PRESETS = {
    all: {
      label: '全ロール',
      roles: ['Viewer', 'Publisher', 'Curator', 'Admin'],
      teams: ['team-platform', 'team-fsi-core'],
    },
    viewer: {
      label: 'Viewer',
      roles: ['Viewer'],
      teams: [],
    },
    publisher: {
      label: 'Publisher',
      roles: ['Viewer', 'Publisher'],
      teams: ['team-platform', 'team-fsi-core'],
    },
    curator: {
      label: 'Curator',
      roles: ['Viewer', 'Curator'],
      teams: ['team-governance'],
    },
    admin: {
      label: 'Admin',
      roles: ['Viewer', 'Admin'],
      teams: ['team-platform-admin'],
    },
  };

  const NAV_SECTIONS = [
    {
      title: '発見',
      roles: '',
      items: [{ key: 'catalog', href: './index.html', label: 'カタログ' }],
    },
    {
      title: '登録（Publisher）',
      roles: 'Publisher',
      items: [{ key: 'drafts', href: './drafts.html', label: '下書き・申請' }],
    },
    {
      title: '審査・公開（Curator）',
      roles: 'Curator',
      items: [
        { key: 'reviews', href: './reviews.html', label: '審査待ち' },
        { key: 'publication', href: './publication.html', label: '公開管理' },
      ],
    },
    {
      title: '管理（Admin）',
      roles: 'Admin',
      items: [
        { key: 'administration', href: './administration.html', label: 'Registry 管理' },
        { key: 'recovery', href: './recovery.html', label: '回復操作' },
      ],
    },
  ];

  function buildDesktopNavHtml(activeNav) {
    const sections = NAV_SECTIONS.map((section) => {
      const roleAttr = section.roles ? ` data-requires-roles="${section.roles}"` : '';
      const itemsHtml = section.items
        .map((item) => {
          const isCurrent = item.key === activeNav;
          const currentAttr = isCurrent ? ' aria-current="page"' : '';
          const cls = isCurrent
            ? 'flex items-center justify-between px-3 py-2 rounded-lg text-sm font-semibold bg-blue-50 text-blue-700'
            : 'flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-100';
          return `<li><a href="${item.href}"${currentAttr} class="${cls}"><span>${item.label}</span></a></li>`;
        })
        .join('');
      return `<div${roleAttr}><div class="px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">${section.title}</div><ul class="space-y-1">${itemsHtml}</ul></div>`;
    }).join('');

    return `${sections}<div class="pt-4 border-t border-slate-200"><div class="px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">プロトタイプ参照</div><ul class="space-y-1"><li><a href="./states.html" class="flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100"><span>状態・デザイン一覧</span></a></li></ul></div>`;
  }

  function buildMobileNavHtml(activeNav) {
    const links = NAV_SECTIONS.flatMap((section) =>
      section.items.map((item) => {
        const isCurrent = item.key === activeNav;
        const roleAttr = section.roles ? ` data-requires-roles="${section.roles}"` : '';
        const currentAttr = isCurrent ? ' aria-current="page"' : '';
        const cls = isCurrent
          ? 'block px-3 py-2 rounded-lg bg-blue-50 text-blue-700 font-semibold'
          : 'block px-3 py-2 rounded-lg text-slate-700 hover:bg-slate-100';
        return `<a href="${item.href}"${roleAttr}${currentAttr} class="${cls}">${item.label}</a>`;
      }),
    ).join('');
    return `${links}<a href="./states.html" class="block px-3 py-2 rounded-lg text-slate-500 hover:bg-slate-100 border-t border-slate-200 pt-3">状態・デザイン一覧</a>`;
  }

  function mountPortalShell() {
    const body = document.body;
    const activeNav = body.getAttribute('data-portal-nav');
    if (!activeNav) return;

    const statesHref = body.getAttribute('data-states-href') || './states.html';
    const statesLabel =
      body.getAttribute('data-states-label') || '状態・エラー・デザインシステム一覧 →';

    const roleButtonsHtml = Object.entries(ROLE_PRESETS)
      .map(
        ([key, preset]) =>
          `<button type="button" data-role-preset="${key}" class="px-2 py-0.5 rounded border border-slate-700 text-xs font-medium cursor-pointer transition">${preset.label}</button>`,
      )
      .join('');

    const topSlot = document.querySelector('[data-portal-shell-top]');
    if (topSlot) {
      topSlot.innerHTML = `
  <!-- プロトタイプ確認用バー（本番 SPA には含めない） -->
  <div class="bg-slate-900 text-slate-200 text-xs px-4 py-2 border-b border-slate-800">
    <div class="max-w-screen-2xl mx-auto flex flex-wrap items-center justify-between gap-2">
      <div class="flex flex-wrap items-center gap-2">
        <span class="inline-flex items-center px-2 py-0.5 rounded bg-blue-500/20 text-blue-200 font-semibold tracking-wide">PROTOTYPE</span>
        <span class="text-slate-400">業務ロール切替:</span>
        <div class="inline-flex flex-wrap gap-1" role="group" aria-label="プロトタイプ業務ロール切替">${roleButtonsHtml}</div>
      </div>
      <div class="flex items-center gap-3">
        <a href="${statesHref}" class="text-blue-300 hover:text-blue-200 underline underline-offset-2">${statesLabel}</a>
      </div>
    </div>
  </div>

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
            <span data-unread-badge class="inline-flex items-center justify-center min-w-5 h-5 px-1.5 text-xs font-semibold rounded-full bg-blue-600 text-white">2</span>
          </button>

          <div id="notifications-popover" data-popover class="is-hidden absolute right-0 mt-2 w-96 max-w-[calc(100vw-2rem)] bg-white rounded-xl border border-slate-200 shadow-xl z-40 overflow-hidden">
            <div class="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
              <div>
                <h2 class="text-sm font-semibold text-slate-900">通知</h2>
                <p class="text-xs text-slate-500">約60秒ごとに自動更新（90日間保持）</p>
              </div>
              <button type="button" data-mark-all-read class="text-xs font-medium text-blue-600 hover:text-blue-700 cursor-pointer">すべて既読にする</button>
            </div>
            <ul class="divide-y divide-slate-100 max-h-80 overflow-y-auto">
              <li data-unread-row class="px-4 py-3 bg-blue-50/50">
                <div class="flex items-start gap-2.5">
                  <span data-unread-dot class="mt-1.5 w-2 h-2 rounded-full bg-blue-600 shrink-0"></span>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-[11px] font-semibold px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">審査待ち</span>
                      <span class="text-xs text-slate-500">12分前</span>
                    </div>
                    <p class="text-sm font-medium text-slate-900 mt-1">引受審査トリアージ Agent（v1.2.0）</p>
                    <p class="text-xs text-slate-600 mt-0.5">team-fsi-core から公開承認の申請が届きました。</p>
                  </div>
                </div>
              </li>
              <li data-unread-row class="px-4 py-3 bg-blue-50/50">
                <div class="flex items-start gap-2.5">
                  <span data-unread-dot class="mt-1.5 w-2 h-2 rounded-full bg-blue-600 shrink-0"></span>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-[11px] font-semibold px-1.5 py-0.5 rounded bg-rose-100 text-rose-800">ポータル外の変更</span>
                      <span class="text-xs text-slate-500">2時間前</span>
                    </div>
                    <p class="text-sm font-medium text-slate-900 mt-1">legacy-billing-mcp（rec881029384）</p>
                    <p class="text-xs text-slate-600 mt-0.5">ポータルの操作記録と一致しない状態変化を検知しました。</p>
                  </div>
                </div>
              </li>
              <li class="px-4 py-3">
                <div class="flex items-start gap-2.5">
                  <span class="mt-1.5 w-2 h-2 rounded-full bg-transparent shrink-0"></span>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-[11px] font-semibold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">公開承認</span>
                      <span class="text-xs text-slate-500">昨日 16:40</span>
                    </div>
                    <p class="text-sm font-medium text-slate-900 mt-1">社内規定検索 MCP サーバー（v2.0.1）</p>
                    <p class="text-xs text-slate-600 mt-0.5">公開承認が完了し、カタログ項目として発見可能になりました。</p>
                  </div>
                </div>
              </li>
            </ul>
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
            <span class="hidden md:inline max-w-44 truncate text-xs font-medium" data-role-summary>Viewer / Publisher / Curator / Admin</span>
            <span class="inline-flex items-center justify-center px-1.5 py-0.5 text-[11px] font-semibold rounded bg-slate-100 text-slate-700" data-role-count>4</span>
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
                <dd class="mt-0.5 text-slate-900 font-medium" data-role-summary>Viewer / Publisher / Curator / Admin</dd>
              </div>
              <div>
                <dt class="text-slate-500 font-medium">所属チーム</dt>
                <dd class="mt-0.5 text-slate-900" data-team-summary>team-platform、team-fsi-core</dd>
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

  function getSavedPreset() {
    try {
      const saved = window.localStorage.getItem(ROLE_STORAGE_KEY);
      if (saved && ROLE_PRESETS[saved]) return saved;
    } catch {
      // file:// 等で localStorage が使えない場合は既定値を使う
    }
    return 'all';
  }

  function savePreset(presetKey) {
    try {
      window.localStorage.setItem(ROLE_STORAGE_KEY, presetKey);
    } catch {
      // 無視
    }
  }

  function applyRolePreset(presetKey) {
    const preset = ROLE_PRESETS[presetKey] || ROLE_PRESETS.all;
    savePreset(presetKey);

    document.querySelectorAll('[data-role-preset]').forEach((btn) => {
      const active = btn.getAttribute('data-role-preset') === presetKey;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.classList.toggle('bg-slate-900', active);
      btn.classList.toggle('text-white', active);
      btn.classList.toggle('bg-white', !active);
      btn.classList.toggle('text-slate-700', !active);
    });

    document.querySelectorAll('[data-requires-roles]').forEach((el) => {
      const required = (el.getAttribute('data-requires-roles') || '')
        .split(/\s+/)
        .filter(Boolean);
      const allowed = required.length === 0 || required.some((r) => preset.roles.includes(r));
      el.classList.toggle('is-hidden', !allowed);
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

  function initRoleBar() {
    document.querySelectorAll('[data-role-preset]').forEach((btn) => {
      btn.addEventListener('click', () => {
        applyRolePreset(btn.getAttribute('data-role-preset') || 'all');
      });
    });
    applyRolePreset(getSavedPreset());
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

  function activateTab(group, btn) {
    const groupName = group.getAttribute('data-tab-group');
    const targetId = btn.getAttribute('data-tab-target');
    const buttons = group.querySelectorAll('[data-tab-target]');
    buttons.forEach((b) => {
      const active = b === btn;
      b.setAttribute('aria-selected', active ? 'true' : 'false');
      b.classList.toggle('border-blue-600', active);
      b.classList.toggle('text-blue-700', active);
      b.classList.toggle('bg-blue-50/70', active);
      b.classList.toggle('border-transparent', !active);
      b.classList.toggle('text-slate-600', !active);
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

  function initCatalogSearchDemo() {
    const form = document.querySelector('[data-catalog-search-form]');
    const browseSection = document.getElementById('catalog-browse-mode');
    const searchSection = document.getElementById('catalog-search-mode');
    const queryDisplay = document.getElementById('catalog-search-query-display');
    const input = document.getElementById('catalog-search-input');
    const backButtons = document.querySelectorAll('[data-catalog-back-to-list]');
    if (!form || !browseSection || !searchSection) return;

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const val = input && 'value' in input ? String(input.value).trim() : '';
      if (!val) {
        if (input && 'focus' in input) input.focus();
        return;
      }
      if (queryDisplay) queryDisplay.textContent = val;
      browseSection.classList.add('is-hidden');
      searchSection.classList.remove('is-hidden');
    });

    backButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        searchSection.classList.add('is-hidden');
        browseSection.classList.remove('is-hidden');
      });
    });
  }

  function initDraftDescriptorDemo() {
    const descriptorSelect = document.getElementById('draft-descriptor');
    if (!descriptorSelect) return;

    const updateDescriptorPanels = () => {
      const selected = 'value' in descriptorSelect ? String(descriptorSelect.value) : '';
      document.querySelectorAll('[data-descriptor-panel]').forEach((panel) => {
        const key = panel.getAttribute('data-descriptor-panel');
        panel.classList.toggle('is-hidden', key !== selected);
      });
    };

    descriptorSelect.addEventListener('change', updateDescriptorPanels);
    updateDescriptorPanels();
  }

  function initRecoveryReconcileDemo() {
    const reconcileBtn = document.querySelector('[data-recovery-reconcile]');
    const unsettledBox = document.getElementById('recovery-unsettled-section');
    if (!reconcileBtn || !unsettledBox) return;

    reconcileBtn.addEventListener('click', () => {
      unsettledBox.classList.remove('is-hidden');
    });
  }

  function initDoubleConfirmGuards() {
    // 廃止モーダルの二重確認（チェックボックス + レコード名一致）
    const deprecateCheck = document.getElementById('deprecate-confirm-check');
    const deprecateName = document.getElementById('deprecate-confirm-name');
    const deprecateSubmit = document.getElementById('deprecate-confirm-submit');
    if (deprecateCheck && deprecateName && deprecateSubmit) {
      const expected = deprecateName.getAttribute('data-expected-name') || '';
      const update = () => {
        const ok =
          'checked' in deprecateCheck &&
          deprecateCheck.checked &&
          'value' in deprecateName &&
          String(deprecateName.value).trim() === expected;
        if ('disabled' in deprecateSubmit) deprecateSubmit.disabled = !ok;
      };
      deprecateCheck.addEventListener('change', update);
      deprecateName.addEventListener('input', update);
      update();
    }

    // 回復操作の強制解除確認（チェックボックス）
    const unlockCheck = document.getElementById('force-unlock-check');
    const unlockSubmit = document.getElementById('force-unlock-submit');
    if (unlockCheck && unlockSubmit) {
      const update = () => {
        if ('disabled' in unlockSubmit) {
          unlockSubmit.disabled = !('checked' in unlockCheck && unlockCheck.checked);
        }
      };
      unlockCheck.addEventListener('change', update);
      update();
    }
  }

  function initNotificationsDemo() {
    document.querySelectorAll('[data-mark-all-read]').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('[data-unread-badge]').forEach((badge) => {
          badge.classList.add('is-hidden');
        });
        document.querySelectorAll('[data-unread-dot]').forEach((dot) => {
          dot.classList.add('is-hidden');
        });
        document.querySelectorAll('[data-unread-row]').forEach((row) => {
          row.classList.remove('bg-blue-50/50');
        });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    mountPortalShell();
    initRoleBar();
    initPopovers();
    initDrawersAndModals();
    initTabs();
    initCatalogSearchDemo();
    initDraftDescriptorDemo();
    initRecoveryReconcileDemo();
    initDoubleConfirmGuards();
    initNotificationsDemo();
  });
})();
