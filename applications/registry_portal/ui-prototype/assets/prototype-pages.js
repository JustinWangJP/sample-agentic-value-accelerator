/**
 * Registry ポータル 静的 HTML プロトタイプの画面固有のデモ動作。
 * 共通シェル（prototype.js）の描画後に動くよう、prototype.js の後に読み込む。
 * 各関数は対象の要素がない画面では何もしない。
 */
(function () {
  'use strict';

  const model = window.RegistryPrototypeModel;
  if (!model) {
    throw new Error('prototype-model.js を prototype-pages.js より前に読み込んでください。');
  }

  /** カタログ: 検索フォーム送信で一覧モードと検索結果モードを切り替える */
  function initCatalogSearchDemo() {
    const form = document.querySelector('[data-catalog-search-form]');
    const browseSection = document.getElementById('catalog-browse-mode');
    const searchSection = document.getElementById('catalog-search-mode');
    const queryDisplay = document.getElementById('catalog-search-query-display');
    const input = document.getElementById('catalog-search-input');
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

    document.querySelectorAll('[data-catalog-back-to-list]').forEach((btn) => {
      btn.addEventListener('click', () => {
        searchSection.classList.add('is-hidden');
        browseSection.classList.remove('is-hidden');
      });
    });
  }

  /** 下書き編集: descriptor 種別の選択に合わせて固有入力欄を切り替える */
  function initDraftDescriptorDemo() {
    const descriptorSelect = document.getElementById('draft-descriptor');
    if (!descriptorSelect) return;

    const updateDescriptorPanels = () => {
      const selected = 'value' in descriptorSelect ? String(descriptorSelect.value) : '';
      document.querySelectorAll('[data-descriptor-panel]').forEach((panel) => {
        panel.classList.toggle('is-hidden', panel.getAttribute('data-descriptor-panel') !== selected);
      });
    };

    descriptorSelect.addEventListener('change', updateDescriptorPanels);
    updateDescriptorPanels();
  }

  /** 回復操作: 再取得照合の後も未確定のときだけ強制解除の欄を出す */
  function initRecoveryReconcileDemo() {
    const reconcileBtn = document.querySelector('[data-recovery-reconcile]');
    const unsettledBox = document.getElementById('recovery-unsettled-section');
    if (!reconcileBtn || !unsettledBox) return;

    reconcileBtn.addEventListener('click', () => {
      unsettledBox.classList.remove('is-hidden');
    });
  }

  function setDisabled(el, disabled) {
    if ('disabled' in el) el.disabled = disabled;
  }

  /** 不可逆操作の二重確認: 条件を満たすまで実行ボタンを無効にする */
  function initDoubleConfirmGuards() {
    // 廃止モーダル（確認チェック + レコード名の一致）
    const deprecateCheck = document.getElementById('deprecate-confirm-check');
    const deprecateName = document.getElementById('deprecate-confirm-name');
    const deprecateSubmit = document.getElementById('deprecate-confirm-submit');
    if (deprecateCheck && deprecateName && deprecateSubmit) {
      const update = () => {
        setDisabled(
          deprecateSubmit,
          !model.isDeprecationConfirmed({
            checked: 'checked' in deprecateCheck && deprecateCheck.checked,
            typedName: 'value' in deprecateName ? deprecateName.value : '',
            expectedName: deprecateName.getAttribute('data-expected-name') || '',
          }),
        );
      };
      deprecateCheck.addEventListener('change', update);
      deprecateName.addEventListener('input', update);
      update();
    }

    // 回復操作の強制解除（確認チェック）
    const unlockCheck = document.getElementById('force-unlock-check');
    const unlockSubmit = document.getElementById('force-unlock-submit');
    if (unlockCheck && unlockSubmit) {
      const update = () => setDisabled(unlockSubmit, !('checked' in unlockCheck && unlockCheck.checked));
      unlockCheck.addEventListener('change', update);
      update();
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    initCatalogSearchDemo();
    initDraftDescriptorDemo();
    initRecoveryReconcileDemo();
    initDoubleConfirmGuards();
  });
})();
