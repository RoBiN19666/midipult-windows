(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const dom = {
    versionPill: $('versionPill'),
    subtitle: $('subtitle'),
    scanButton: $('scanButton'),
    scanLabel: $('scanLabel'),
    addButton: $('addButton'),
    emptyScan: $('emptyScan'),
    emptyAdd: $('emptyAdd'),
    empty: $('empty'),
    stations: $('stations'),
    status: $('status'),
    banner: $('banner'),
    bannerText: $('bannerText'),
    bannerDetail: $('bannerDetail'),
    bannerRetry: $('bannerRetry'),
    bannerClose: $('bannerClose'),
    dataFolder: $('dataFolder'),
    toast: $('toast'),
    addDialog: $('addDialog'),
    addForm: $('addForm'),
    addAddress: $('addAddress'),
    addName: $('addName'),
    addError: $('addError'),
    addSubmit: $('addSubmit'),
    renameDialog: $('renameDialog'),
    renameForm: $('renameForm'),
    renameName: $('renameName'),
    renameAddress: $('renameAddress'),
    renameError: $('renameError'),
    deleteDialog: $('deleteDialog'),
    deleteForm: $('deleteForm'),
    deleteName: $('deleteName'),
    aboutButton: $('aboutButton'),
    aboutDialog: $('aboutDialog'),
    aboutVersion: $('aboutVersion'),
    updateButton: $('updateButton'),
    updateDialog: $('updateDialog'),
    updateLead: $('updateLead'),
    updateDetail: $('updateDetail'),
    updateInstall: $('updateInstall'),
    updatePage: $('updatePage'),
  };

  const state = {
    stations: [],
    active_id: '',
    version: '1.0.0',
    data_dir: '',
  };

  let booted = false;
  let busy = false;
  let retryStationId = '';
  let toastTimer = 0;
  let statusTimer = 0;
  let editingStationId = '';
  let deletingStationId = '';

  // ------------------------------------------------------------------
  // Запуск
  // ------------------------------------------------------------------

  window.addEventListener('pywebviewready', boot);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', waitForBridge);
  } else {
    waitForBridge();
  }

  function bridgeReady() {
    return !!(window.pywebview && window.pywebview.api &&
      typeof window.pywebview.api.get_state === 'function');
  }

  function waitForBridge() {
    if (bridgeReady()) {
      boot();
      return;
    }
    let attempts = 0;
    const timer = setInterval(function () {
      attempts += 1;
      if (bridgeReady()) {
        clearInterval(timer);
        boot();
      } else if (attempts > 200) {
        clearInterval(timer);
        showBanner('Не удалось связаться с приложением',
          'Перезапустите MidiPult. Если ошибка повторяется, посмотрите midipult.log в папке настроек.');
      }
    }, 50);
  }

  function boot() {
    if (booted) return;
    booted = true;
    bindEvents();

    call('get_state')
      .then(function (result) {
        applyState(result);
        if (!result || !result.stations || result.stations.length === 0) {
          setStatus('Колонок пока нет — запустите поиск или добавьте адрес вручную.');
        }
      })
      .catch(function (error) {
        showBanner('Не удалось загрузить список колонок', String(error && error.message || error));
      });
  }

  async function call(name) {
    const bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge[name] !== 'function') {
      throw new Error('Мост приложения ещё не готов');
    }
    const args = Array.prototype.slice.call(arguments, 1);
    return bridge[name].apply(bridge, args);
  }

  // ------------------------------------------------------------------
  // Состояние и отрисовка
  // ------------------------------------------------------------------

  function applyState(payload) {
    if (!payload) return;
    if (Array.isArray(payload.stations)) state.stations = payload.stations;
    if (typeof payload.active_id === 'string') state.active_id = payload.active_id;
    if (payload.version) {
      state.version = payload.version;
      dom.versionPill.textContent = 'v' + payload.version;
      dom.aboutVersion.textContent = payload.version;
    }
    if (payload.data_dir) {
      state.data_dir = payload.data_dir;
      dom.dataFolder.title = payload.data_dir;
    }
    render();
  }

  function render() {
    dom.stations.replaceChildren();

    const count = state.stations.length;
    if (count === 0) {
      dom.empty.hidden = false;
      dom.subtitle.textContent = 'Выберите колонку или найдите её в локальной сети';
      return;
    }

    dom.empty.hidden = true;
    dom.subtitle.textContent = count === 1 ? '1 сохранённая колонка' : count + ' сохранённых колонок';

    state.stations.forEach(function (station) {
      dom.stations.appendChild(buildCard(station));
    });
  }

  function buildCard(station) {
    const card = el('article', 'station-card');
    if (station.id === state.active_id) card.classList.add('active');

    const top = el('div', 'card-top');
    top.appendChild(el('div', 'station-avatar'));

    const heading = el('div', 'card-heading');
    const name = el('h3', 'card-name', station.name || 'Колонка');
    name.title = station.name || '';
    heading.appendChild(name);

    const url = el('p', 'card-url', readableUrl(station.url));
    url.title = station.url || '';
    heading.appendChild(url);
    top.appendChild(heading);

    if (station.id === state.active_id) {
      top.appendChild(el('span', 'active-badge', 'Активна'));
    }
    card.appendChild(top);

    const meta = el('div', 'card-meta');
    if (station.model) meta.appendChild(el('span', 'chip', station.model));
    if (station.version) meta.appendChild(el('span', 'chip', 'панель ' + station.version));
    if (station.ip) meta.appendChild(el('span', 'chip', station.ip));
    if (!meta.childElementCount) meta.appendChild(el('span', 'chip', 'адрес сохранён вручную'));
    card.appendChild(meta);

    const actions = el('div', 'card-actions');

    const open = el('button', 'btn btn-open', 'Открыть');
    open.type = 'button';
    open.addEventListener('click', function () { openStation(station.id); });
    actions.appendChild(open);

    const rename = el('button', 'icon-button', '✎');
    rename.type = 'button';
    rename.title = 'Переименовать';
    rename.setAttribute('aria-label', 'Переименовать колонку');
    rename.addEventListener('click', function () { openRename(station); });
    actions.appendChild(rename);

    const remove = el('button', 'icon-button danger', '✕');
    remove.type = 'button';
    remove.title = 'Удалить из списка';
    remove.setAttribute('aria-label', 'Удалить колонку');
    remove.addEventListener('click', function () { openDelete(station); });
    actions.appendChild(remove);

    card.appendChild(actions);
    return card;
  }

  function readableUrl(value) {
    if (!value) return '';
    try {
      const parsed = new URL(value);
      return parsed.host + (parsed.pathname && parsed.pathname !== '/' ? parsed.pathname : '');
    } catch (error) {
      return value;
    }
  }

  // ------------------------------------------------------------------
  // Действия
  // ------------------------------------------------------------------

  async function scan() {
    if (busy) return;
    hideBanner();
    setBusy(true);
    setStatus('Ищу колонки в локальной сети…', { sticky: true });
    try {
      const result = await call('scan');
      if (result && result.state) applyState(result.state);
      if (!result || !result.ok) {
        setStatus('');
        showBanner(result && result.error || 'Не удалось выполнить поиск',
          result && result.detail || '');
        return;
      }
      const found = Number(result.found) || 0;
      if (found === 0) {
        setStatus('Колонок не найдено. Убедитесь, что панель запущена и компьютер в той же сети, ' +
          'либо добавьте адрес вручную.');
      } else {
        setStatus('Найдено колонок: ' + found + '. Список обновлён.');
        clearStatusAfter(6000);
      }
    } catch (error) {
      setStatus('');
      showBanner('Ошибка поиска', String(error && error.message || error));
    } finally {
      setBusy(false);
    }
  }

  async function openStation(stationId) {
    if (busy) return;
    const station = findStation(stationId);
    const label = station ? station.name || station.url : 'колонке';
    retryStationId = '';
    hideBanner();
    setBusy(true);
    setStatus('Подключаюсь к «' + label + '»…', { sticky: true });
    try {
      const result = await call('open_station', stationId);
      if (!result || !result.ok) {
        setStatus('');
        showBanner(result && result.error || 'Не удалось открыть панель',
          'Проверьте, что колонка включена и панель MidiPult запущена.', stationId);
        return;
      }
      state.active_id = stationId;
      render();
      setStatus('Открываю панель…', { sticky: true });
    } catch (error) {
      setStatus('');
      showBanner('Не удалось открыть панель', String(error && error.message || error), stationId);
    } finally {
      setBusy(false);
    }
  }

  async function submitAdd(event) {
    event.preventDefault();
    const address = dom.addAddress.value.trim();
    const name = dom.addName.value.trim();
    if (!address) {
      showFormError(dom.addError, 'Укажите адрес колонки');
      dom.addAddress.focus();
      return;
    }

    setFormError(dom.addError, '');
    dom.addSubmit.disabled = true;
    try {
      const result = await call('add_station', address, name);
      if (!result || !result.ok) {
        showFormError(dom.addError, result && result.error || 'Не удалось добавить колонку');
        return;
      }
      applyState(result.state);
      dom.addDialog.close();
      toast('Колонка добавлена');
      setStatus('Колонка «' + (result.station && result.station.name || address) + '» добавлена в список.');
      clearStatusAfter(5000);
    } catch (error) {
      showFormError(dom.addError, String(error && error.message || error));
    } finally {
      dom.addSubmit.disabled = false;
    }
  }

  async function submitRename(event) {
    event.preventDefault();
    const name = dom.renameName.value.trim();
    if (!name) {
      showFormError(dom.renameError, 'Название не может быть пустым');
      dom.renameName.focus();
      return;
    }
    setFormError(dom.renameError, '');
    try {
      const result = await call('rename_station', editingStationId, name);
      if (!result || !result.ok) {
        showFormError(dom.renameError, result && result.error || 'Не удалось переименовать');
        return;
      }
      applyState(result.state);
      dom.renameDialog.close();
      toast('Название сохранено');
    } catch (error) {
      showFormError(dom.renameError, String(error && error.message || error));
    }
  }

  async function submitDelete(event) {
    event.preventDefault();
    try {
      const result = await call('delete_station', deletingStationId);
      if (!result || !result.ok) {
        toast(result && result.error || 'Не удалось удалить колонку', true);
        return;
      }
      applyState(result.state);
      dom.deleteDialog.close();
      toast('Колонка удалена из списка');
    } catch (error) {
      toast(String(error && error.message || error), true);
    }
  }

  // ------------------------------------------------------------------
  // Диалоги
  // ------------------------------------------------------------------

  function openAdd() {
    setFormError(dom.addError, '');
    dom.addForm.reset();
    dom.addDialog.showModal();
    setTimeout(function () { dom.addAddress.focus(); }, 30);
  }

  function openRename(station) {
    editingStationId = station.id;
    setFormError(dom.renameError, '');
    dom.renameName.value = station.name || '';
    dom.renameAddress.textContent = readableUrl(station.url);
    dom.renameDialog.showModal();
    setTimeout(function () {
      dom.renameName.focus();
      dom.renameName.select();
    }, 30);
  }

  function openDelete(station) {
    deletingStationId = station.id;
    dom.deleteName.textContent = station.name || readableUrl(station.url);
    dom.deleteDialog.showModal();
  }

  function openAbout() {
    dom.aboutDialog.showModal();
  }

  // ------------------------------------------------------------------
  // Обновления
  // ------------------------------------------------------------------

  async function checkUpdates() {
    dom.updateInstall.hidden = true;
    dom.updateInstall.disabled = false;
    dom.updatePage.hidden = true;
    dom.updateLead.textContent = 'Проверяю обновления…';
    dom.updateDetail.textContent = '';
    dom.updateDialog.showModal();

    let result;
    try {
      result = await call('check_update');
    } catch (error) {
      result = { ok: false, error: String(error && error.message || error) };
    }

    if (!result || !result.ok) {
      dom.updateLead.textContent = 'Не удалось проверить обновления';
      dom.updateDetail.textContent = (result && result.error) || 'Попробуйте позже.';
      dom.updatePage.hidden = false;
      return;
    }

    if (!result.available) {
      dom.updateLead.textContent = 'Установлена последняя версия — ' + result.latest;
      dom.updateDetail.textContent = 'Обновление не требуется.';
      return;
    }

    dom.updateLead.textContent = 'Доступна версия ' + result.latest;
    if (result.can_install) {
      dom.updateDetail.textContent = result.installed
        ? 'Сейчас ' + result.current + '. Обновление скачается с GitHub, SHA-256 проверится ' +
          'автоматически, установщик обновит приложение поверх — настройки сохранятся.'
        : 'Портативная версия ' + result.current + '. Новый файл заменит текущий, ' +
          'список колонок и настройки останутся на месте.';
      dom.updateInstall.hidden = false;
    } else {
      dom.updateDetail.textContent =
        'В релизе нет файла для этого режима работы — скачайте вручную со страницы релиза.';
    }
    dom.updatePage.hidden = false;
  }

  async function installUpdate() {
    dom.updateInstall.disabled = true;
    dom.updatePage.hidden = true;
    dom.updateLead.textContent = 'Скачиваю обновление…';
    dom.updateDetail.textContent = 'Не закрывайте приложение. Проверяю SHA-256.';

    let result;
    try {
      result = await call('install_update');
    } catch (error) {
      result = { ok: false, error: String(error && error.message || error) };
    }

    if (!result || !result.ok) {
      dom.updateLead.textContent = 'Обновление не установлено';
      dom.updateDetail.textContent = (result && result.error) || 'Попробуйте позже.';
      dom.updateInstall.disabled = false;
      dom.updatePage.hidden = false;
      return;
    }

    dom.updateLead.textContent = 'Устанавливаю…';
    dom.updateDetail.textContent = 'Приложение перезапустится автоматически.';
  }

  function showFormError(node, text) {
    node.textContent = text || '';
    node.hidden = !text;
  }

  function setFormError(node, text) {
    showFormError(node, text);
  }

  // ------------------------------------------------------------------
  // Статус, баннер, уведомления
  // ------------------------------------------------------------------

  function setStatus(text, options) {
    window.clearTimeout(statusTimer);
    if (!text) {
      dom.status.hidden = true;
      dom.status.textContent = '';
      return;
    }
    dom.status.hidden = false;
    dom.status.textContent = text;
    if (!options || !options.sticky) clearStatusAfter(6000);
  }

  function clearStatusAfter(delay) {
    window.clearTimeout(statusTimer);
    statusTimer = window.setTimeout(function () {
      dom.status.hidden = true;
      dom.status.textContent = '';
    }, delay);
  }

  function showBanner(text, detail, stationId) {
    dom.bannerText.textContent = text || '';
    dom.bannerDetail.textContent = detail || '';
    dom.bannerDetail.hidden = !detail;
    retryStationId = stationId || '';
    dom.bannerRetry.hidden = !retryStationId;
    dom.banner.hidden = false;
  }

  function hideBanner() {
    dom.banner.hidden = true;
    retryStationId = '';
  }

  function toast(text, isError) {
    window.clearTimeout(toastTimer);
    dom.toast.textContent = text || '';
    dom.toast.classList.toggle('error', !!isError);
    dom.toast.classList.add('show');
    toastTimer = window.setTimeout(function () {
      dom.toast.classList.remove('show');
    }, 3200);
  }

  function setBusy(value) {
    busy = !!value;
    dom.scanButton.disabled = busy;
    dom.emptyScan.disabled = busy;
    dom.scanButton.classList.toggle('scanning', busy);
    dom.scanLabel.textContent = busy ? 'Поиск…' : 'Найти колонки';
  }

  // ------------------------------------------------------------------
  // События
  // ------------------------------------------------------------------

  function bindEvents() {
    dom.scanButton.addEventListener('click', scan);
    dom.emptyScan.addEventListener('click', scan);
    dom.addButton.addEventListener('click', openAdd);
    dom.emptyAdd.addEventListener('click', openAdd);

    dom.addForm.addEventListener('submit', submitAdd);
    dom.renameForm.addEventListener('submit', submitRename);
    dom.deleteForm.addEventListener('submit', submitDelete);

    dom.bannerClose.addEventListener('click', hideBanner);
    dom.bannerRetry.addEventListener('click', function () {
      if (retryStationId) openStation(retryStationId);
    });

    dom.dataFolder.addEventListener('click', function () {
      call('open_data_folder').catch(function (error) {
        toast(String(error && error.message || error), true);
      });
    });

    dom.aboutButton.addEventListener('click', openAbout);
    dom.updateButton.addEventListener('click', checkUpdates);
    dom.updateInstall.addEventListener('click', installUpdate);
    dom.updatePage.addEventListener('click', function () {
      call('open_release_page').catch(function (error) {
        toast(String(error && error.message || error), true);
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll('[data-close]'), function (button) {
      button.addEventListener('click', function () {
        const dialog = button.closest('dialog');
        if (dialog) dialog.close();
      });
    });

    [dom.addDialog, dom.renameDialog, dom.deleteDialog, dom.aboutDialog, dom.updateDialog].forEach(function (dialog) {
      dialog.addEventListener('click', function (event) {
        if (event.target === dialog) dialog.close();
      });
      dialog.addEventListener('close', function () {
        setFormError(dom.addError, '');
        setFormError(dom.renameError, '');
      });
    });

    document.addEventListener('keydown', function (event) {
      const key = (event.key || '').toLowerCase();
      if (event.key === 'F5' || (event.ctrlKey && key === 'r')) {
        event.preventDefault();
        scan();
      } else if (event.ctrlKey && key === 'n') {
        event.preventDefault();
        openAdd();
      }
    });
  }

  // ------------------------------------------------------------------
  // Вспомогательное
  // ------------------------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function findStation(stationId) {
    for (let i = 0; i < state.stations.length; i += 1) {
      if (state.stations[i].id === stationId) return state.stations[i];
    }
    return null;
  }
})();
