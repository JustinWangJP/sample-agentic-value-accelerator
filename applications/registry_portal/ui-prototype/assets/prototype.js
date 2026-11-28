/**
 * Registry ポータル 静的 HTML プロトタイプ用の共通インタラクション。
 * 本番 CSP（script-src 'self' / インラインスクリプト不可）に準拠し、
 * すべての動作を data-* 属性のイベントリスナーで制御する。
 */
(function () {
  'use strict';

  const ROLE_STORAGE_KEY = 'registry-portal-prototype-role';

  const ROLE_PRESETS = {
    all: {
      label: '全ロール（確認用）',
      roles: ['Viewer', 'Publisher', 'Curator', 'Admin'],
      teams: ['team-platform', 'team-fsi-core'],
    },
    viewer: {
      label: 'Viewer のみ',
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

    // ツールバーのボタン状態を更新
    document.querySelectorAll('[data-role-preset]').forEach((btn) => {
      const active = btn.getAttribute('data-role-preset') === presetKey;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.classList.toggle('bg-slate-900', active);
      btn.classList.toggle('text-white', active);
      btn.classList.toggle('bg-white', !active);
      btn.classList.toggle('text-slate-700', !active);
    });

    // ロール依存のナビゲーションや要素を出し分け
    document.querySelectorAll('[data-requires-roles]').forEach((el) => {
      const required = (el.getAttribute('data-requires-roles') || '')
        .split(/\s+/)
        .filter(Boolean);
      const allowed = required.length === 0 || required.some((r) => preset.roles.includes(r));
      el.classList.toggle('is-hidden', !allowed);
    });

    // ヘッダーのロール・チーム表示を更新
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

  function closeDrawer(drawer) {
    if (!drawer) return;
    drawer.classList.add('is-hidden');
    drawer.setAttribute('aria-hidden', 'true');
  }

  function openDrawer(id) {
    const drawer = document.getElementById(id);
    if (!drawer) return;
    drawer.classList.remove('is-hidden');
    drawer.setAttribute('aria-hidden', 'false');
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.classList.add('is-hidden');
    modal.setAttribute('aria-hidden', 'true');
  }

  function openModal(id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.remove('is-hidden');
    modal.setAttribute('aria-hidden', 'false');
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
    document.querySelectorAll('[data-open-drawer]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-open-drawer');
        if (id) openDrawer(id);
      });
    });

    document.querySelectorAll('[data-close-drawer]').forEach((btn) => {
      btn.addEventListener('click', () => {
        closeDrawer(btn.closest('[data-drawer]'));
      });
    });

    document.querySelectorAll('[data-open-modal]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-open-modal');
        if (id) openModal(id);
      });
    });

    document.querySelectorAll('[data-close-modal]').forEach((btn) => {
      btn.addEventListener('click', () => {
        closeModal(btn.closest('[data-modal]'));
      });
    });

    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      closeAllPopovers(null);
      document.querySelectorAll('[data-drawer]:not(.is-hidden)').forEach(closeDrawer);
      document.querySelectorAll('[data-modal]:not(.is-hidden)').forEach(closeModal);
    });
  }

  function initTabs() {
    document.querySelectorAll('[data-tab-group]').forEach((group) => {
      const groupName = group.getAttribute('data-tab-group');
      const buttons = group.querySelectorAll('[data-tab-target]');
      buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
          const targetId = btn.getAttribute('data-tab-target');
          buttons.forEach((b) => {
            const active = b === btn;
            b.setAttribute('aria-selected', active ? 'true' : 'false');
            b.classList.toggle('border-blue-600', active);
            b.classList.toggle('text-blue-700', active);
            b.classList.toggle('bg-blue-50/70', active);
            b.classList.toggle('border-transparent', !active);
            b.classList.toggle('text-slate-600', !active);
          });
          document
            .querySelectorAll(`[data-tab-panel-group="${groupName}"]`)
            .forEach((panel) => {
              panel.classList.toggle('is-hidden', panel.id !== targetId);
            });
        });
      });
    });
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
    initRoleBar();
    initPopovers();
    initDrawersAndModals();
    initTabs();
    initCatalogSearchDemo();
    initDoubleConfirmGuards();
    initNotificationsDemo();
  });
})();
