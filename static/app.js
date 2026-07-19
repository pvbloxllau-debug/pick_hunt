
        /* ===== PICKER HUNT - Core JS (ES5, sin arrow functions ni template literals) ===== */

        /* Desregistrar Service Worker anterior para evitar doble carga de pagina */
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.getRegistrations().then(function(regs) {
                regs.forEach(function(reg) { reg.unregister(); });
            });
        }

        /* --- Timer Engine --- */
        var PHASE1_MS = 15 * 60 * 1000;
        var PHASE2_MS =  5 * 60 * 1000;
        var TOTAL_MS  = PHASE1_MS + PHASE2_MS;

        function fmtMM_SS(ms) {
            var totalSec = Math.floor(Math.abs(ms) / 1000);
            var m = Math.floor(totalSec / 60).toString();
            var s = (totalSec % 60).toString();
            if (m.length < 2) m = '0' + m;
            if (s.length < 2) s = '0' + s;
            return m + ':' + s;
        }

        function tickTimers() {
            var nowMs = Date.now();
            var cards = document.querySelectorAll('[data-reported-unix]');
            for (var i = 0; i < cards.length; i++) {
                var card = cards[i];
                var unix = parseInt(card.dataset.reportedUnix, 10);
                var role = card.dataset.userRole || '';
                if (!unix || isNaN(unix)) continue;
                var elapsedMs = nowMs - (unix * 1000);
                if (elapsedMs < 0) continue;
                var timerLabel  = card.querySelector('.hunt-timer-label');
                var phase2Zone  = card.querySelector('.hunt-phase2-zone');
                var phase3Zone  = card.querySelector('.hunt-phase3-zone');
                var pickerP3    = card.querySelector('.hunt-picker-phase3-zone');
                var progressBar = card.querySelector('.hunt-progress-bar');
                if (!timerLabel) continue;
                var phase = elapsedMs < PHASE1_MS ? 1 : elapsedMs < TOTAL_MS ? 2 : 3;
                var prevPhase = parseInt(card.dataset.timerPhase || '0', 10);
                if (phase === 1) {
                    var rem = PHASE1_MS - elapsedMs;
                    if (prevPhase !== 1) {
                        timerLabel.style.cssText = 'font-size:10px;font-family:monospace;font-weight:700;padding:2px 8px;border-radius:9999px;white-space:nowrap;color:white;background:#16a34a;';
                        if (phase2Zone) phase2Zone.classList.add('hidden');
                        if (phase3Zone) phase3Zone.classList.add('hidden');
                        if (pickerP3)   pickerP3.classList.add('hidden');
                        card.dataset.timerPhase = '1';
                    }
                    timerLabel.textContent = fmtMM_SS(rem);
                } else if (phase === 2) {
                    var rem2 = TOTAL_MS - elapsedMs;
                    var pct  = Math.max(0, (rem2 / PHASE2_MS) * 100);
                    if (prevPhase !== 2) {
                        timerLabel.style.cssText = 'font-size:10px;font-family:monospace;font-weight:700;padding:2px 8px;border-radius:9999px;white-space:nowrap;color:white;background:#dc2626;letter-spacing:0.04em;';
                        if (phase2Zone) phase2Zone.classList.remove('hidden');
                        if (phase3Zone) phase3Zone.classList.add('hidden');
                        if (pickerP3)   pickerP3.classList.add('hidden');
                        card.dataset.timerPhase = '2';
                    }
                    timerLabel.textContent = fmtMM_SS(rem2);
                    if (progressBar) progressBar.style.width = pct + '%';
                } else {
                    if (prevPhase !== 3) {
                        timerLabel.style.cssText = 'font-size:10px;font-family:monospace;font-weight:700;padding:2px 8px;border-radius:9999px;white-space:nowrap;color:white;background:#dc2626;animation:pulse 1s infinite;';
                        timerLabel.textContent = 'VENCIDO';
                        if (phase2Zone) phase2Zone.classList.add('hidden');
                        if (phase3Zone && role !== 'picker') phase3Zone.classList.remove('hidden');
                        if (pickerP3   && role === 'picker') pickerP3.classList.remove('hidden');
                        card.dataset.timerPhase = '3';
                    }
                }
            }
        }
        setInterval(tickTimers, 1000);
        document.addEventListener('htmx:afterSettle', tickTimers);

        /* --- Indicador JS vivo + carga inicial hunts via fetch directo --- */
        var _jsAliveCount = 0;
        var _searchTimer  = null;

        function loadHunts(q) {
            var container = document.getElementById('hunts-container');
            if (!container) return;
            clearTimeout(_searchTimer);
            _searchTimer = setTimeout(function() {
                var url = '/api/hunts-list?search=' + encodeURIComponent(q || '');
                fetch(url, {credentials: 'same-origin'})
                    .then(function(r) { return r.text(); })
                    .then(function(html) {
                        container.innerHTML = html;
                        tickTimers();
                        if (typeof htmx !== 'undefined') htmx.process(container);
                    })
                    .catch(function(e) {
                        container.innerHTML = '<p style="color:red;font-size:11px;padding:8px;">Error cargando hunts: ' + e.message + '</p>';
                    });
            }, q ? 300 : 0);
        }

        /* === INICIALIZACION DIRECTA === */
        /* Script esta al final del body: DOM completamente listo, sin depender de eventos */

        /* 1. Indicador de vida sincronico: si el browser llega aqui, JS corre */
        (function() {
            var el = document.getElementById('js-alive');
            if (el) { el.textContent = 'JS VIVO'; el.style.color = '#16a34a'; el.style.fontWeight = '900'; }
        })();

        /* 2. HTMX: procesar los hunt cards pre-renderizados por el servidor */
        if (typeof htmx !== 'undefined') {
            htmx.process(document.getElementById('hunts-container') || document.body);
        }

        /* 3. Timer: correr inmediatamente sobre los cards ya en el DOM */
        tickTimers();

        /* 4. Indicador tick cada segundo */
        setInterval(function() {
            _jsAliveCount++;
            var el = document.getElementById('js-alive');
            if (el) { el.textContent = 'JS ' + _jsAliveCount + 's'; el.style.color = '#16a34a'; }
        }, 1000);

        /* 5. Recargar hunts desde servidor cada 30 seg (keepalive sin depender de WS) */
        setInterval(function() {
            var q = document.getElementById('search-input');
            loadHunts(q ? q.value : '');
        }, 30000);

        /* 6. Re-procesar HTMX y timers cuando HTMX inyecta contenido nuevo */
        document.addEventListener('htmx:afterSettle', function(e) {
            tickTimers();
            if (typeof htmx !== 'undefined') {
                htmx.process(e.detail && e.detail.elt ? e.detail.elt : document.body);
            }
        });

        /* Inyectar keyframes slideInRight una sola vez */
        (function() {
            if (document.getElementById('toast-style')) return;
            var s = document.createElement('style');
            s.id = 'toast-style';
            s.textContent = '@keyframes slideInRight{from{opacity:0;transform:translateX(60px)}to{opacity:1;transform:translateX(0)}}';
            document.head.appendChild(s);
        })();

        function showToast(msg, color) {
            var colors = {
                green:  'background:#15803d;',
                red:    'background:#dc2626;',
                orange: 'background:#ea580c;',
                blue:   'background:#2563eb;',
                yellow: 'background:#ca8a04;'
            };
            var bg = colors[color] || colors.green;
            var existing = document.querySelectorAll('.app-toast');
            var offset = 16 + existing.length * 68;
            var t = document.createElement('div');
            t.className = 'app-toast';
            t.style.cssText = 'position:fixed;top:' + offset + 'px;right:16px;' + bg + 'color:white;padding:12px 16px;border-radius:12px;font-size:13px;font-weight:700;z-index:99999;max-width:300px;text-align:left;box-shadow:0 6px 20px rgba(0,0,0,.25);animation:slideInRight .2s ease;line-height:1.4;';
            t.textContent = msg;
            document.body.appendChild(t);
            setTimeout(function() { if (t.parentNode) t.parentNode.removeChild(t); }, 3500);
        }

        /* Toast pulsante para broadcasts (supervisor/hunter) - persiste 8 seg */
        function showPulseToast(msg, color) {
            var colors = {
                green:  'background:#15803d;',
                red:    'background:#dc2626;',
                orange: 'background:#ea580c;',
                blue:   'background:#1d4ed8;',
                yellow: 'background:#ca8a04;'
            };
            var bg = colors[color] || colors.blue;
            var existing = document.querySelectorAll('.app-toast');
            var offset = 16 + existing.length * 68;
            var t = document.createElement('div');
            t.className = 'app-toast';
            /* slide-in, luego cambia a pulse */
            t.style.cssText = 'position:fixed;top:' + offset + 'px;right:16px;' + bg +
                'color:white;padding:12px 16px;border-radius:12px;font-size:13px;font-weight:700;' +
                'z-index:99999;max-width:300px;text-align:left;box-shadow:0 6px 20px rgba(0,0,0,.3);' +
                'animation:slideInRight .2s ease;line-height:1.4;cursor:pointer;';
            t.textContent = msg;
            t.onclick = function() { if (t.parentNode) t.parentNode.removeChild(t); };
            document.body.appendChild(t);
            /* tras slide-in, activar pulse */
            setTimeout(function() {
                if (t.parentNode) t.style.animation = 'pulse 1.5s infinite';
            }, 250);
            /* auto-remove a los 8 seg */
            setTimeout(function() { if (t.parentNode) t.parentNode.removeChild(t); }, 8000);
        }

        /* --- Report Modal --- */
        function openReportModal() {
            var modal = document.getElementById('mobile-report-modal');
            if (!modal) return;
            modal.classList.remove('hidden');
            setTimeout(function() {
                modal.classList.remove('opacity-0');
                var content = modal.firstElementChild;
                if (content) content.classList.remove('translate-y-full');
            }, 10);
        }
        function closeReportModal() {
            var modal = document.getElementById('mobile-report-modal');
            if (!modal) return;
            modal.classList.add('opacity-0');
            var content = modal.firstElementChild;
            if (content) content.classList.add('translate-y-full');
            setTimeout(function() { modal.classList.add('hidden'); }, 300);
        }
        function submitReportModal(event) {
            if (event) event.preventDefault();
            var form = document.getElementById('mobile-report-form');
            if (!form) return;
            var formData = new FormData(form);
            fetch('/api/hunts', { method: 'POST', body: formData })
                .then(function(res) { return res.text(); })
                .then(function(html) {
                    var c = document.getElementById('hunts-container');
                    if (c) { c.innerHTML = html; if (typeof htmx !== 'undefined') htmx.process(c); tickTimers(); }
                    closeReportModal();
                    form.reset();
                    var df = document.getElementById('desktop-report-form');
                    if (df) df.reset();
                    var fc = document.getElementById('feed-container');
                    if (fc && typeof htmx !== 'undefined') htmx.trigger(fc, 'reload-feed');
                })
                .catch(function(e) { showToast('Error al reportar: ' + e.message, 'red'); });
            return false;
        }

        /* --- Not Found Modal --- */
        function openNotFoundModal(huntId, itemName) {
            var el = document.getElementById('nf-item-name');
            if (el) el.textContent = itemName;
            var frm = document.getElementById('not-found-form');
            if (frm) frm.dataset.huntId = huntId;
            var modal = document.getElementById('not-found-modal');
            if (modal) modal.classList.remove('hidden');
        }
        function closeNotFoundModal() {
            var modal = document.getElementById('not-found-modal');
            if (modal) modal.classList.add('hidden');
            var frm = document.getElementById('not-found-form');
            if (frm) frm.reset();
        }

        /* --- Adjust Modal --- */
        function openAdjustModal(huntId, itemName, qty) {
            var el = document.getElementById('adj-item-name');
            if (el) el.textContent = itemName;
            var qEl = document.getElementById('adj-qty');
            if (qEl) qEl.value = qty;
            var frm = document.getElementById('adjust-form');
            if (frm) frm.dataset.huntId = huntId;
            var modal = document.getElementById('adjust-modal');
            if (modal) modal.classList.remove('hidden');
        }
        function closeAdjustModal() {
            var modal = document.getElementById('adjust-modal');
            if (modal) modal.classList.add('hidden');
            var frm = document.getElementById('adjust-form');
            if (frm) frm.reset();
        }

        /* --- Generic resolution form submit --- */
        function submitResolutionForm(formId, closeFn) {
            var form = document.getElementById(formId);
            if (!form) return false;
            var hid  = form.dataset.huntId;
            var action = form.getAttribute('action').replace('__HID__', hid);
            var data = new FormData(form);
            fetch(action, { method: 'POST', body: data })
                .then(function(r) { return r.text(); })
                .then(function(html) {
                    var c = document.getElementById('hunts-container');
                    if (c) { c.innerHTML = html; if (typeof htmx !== 'undefined') htmx.process(c); tickTimers(); }
                    if (closeFn) closeFn();
                    var fc = document.getElementById('feed-container');
                    if (fc && typeof htmx !== 'undefined') htmx.trigger(fc, 'reload-feed');
                });
            return false;
        }

        /* --- Picker confirma retiro del producto --- */
        function pickerConfirm(huntId) {
            fetch('/api/hunts/' + huntId + '/picker-confirm', { method: 'POST', credentials: 'same-origin' })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (d.ok) {
                        showToast('Retiro confirmado', 'green');
                        // Recargar alertas para mostrar badge Retirado
                        var q = document.getElementById('search-input');
                        loadHunts(q ? q.value : '');
                    }
                })
                .catch(function() { showToast('Error de red', 'red'); });
        }

        /* --- Warn Hunter (picker llama al hunter) --- */
        function warnHunter(huntId) {
            fetch('/api/hunts/' + huntId + '/warn-hunter', { method: 'POST' })
                .then(function(r) { return r.json(); })
                .then(function(d) { showToast(d.message || 'Aviso enviado', 'orange'); })
                .catch(function() { showToast('Error de red', 'red'); });
        }

        /* --- Protocolo: picker aplica quiebre --- */
        function aplicoProtocolo(huntId, itemName) {
            showToast('Registrando protocolo...', 'blue');
            fetch('/api/hunts/' + huntId + '/aplico-protocolo', { method: 'POST' })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    showToast(d.message || 'Protocolo registrado', 'green');
                    var c = document.getElementById('hunts-container');
                    if (c && typeof htmx !== 'undefined') htmx.trigger(c, 'reload-hunts');
                })
                .catch(function() { showToast('Error de red', 'red'); });
        }

        /* --- Protocolo: hunter confirma --- */
        function confirmarProtocolo(huntId) {
            fetch('/api/hunts/' + huntId + '/confirmar-protocolo', { method: 'POST' })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    showToast(d.message || 'Protocolo confirmado', 'green');
                    var m = document.getElementById('protocolo-modal');
                    if (m) m.remove();
                    var c = document.getElementById('hunts-container');
                    if (c && typeof htmx !== 'undefined') htmx.trigger(c, 'reload-hunts');
                })
                .catch(function() { showToast('Error de red', 'red'); });
        }

        /* --- Mantener busqueda --- */
        function mantenerBusqueda(huntId) {
            fetch('/api/hunts/' + huntId + '/mantener-busqueda', { method: 'POST' })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    showToast(d.message || 'Busqueda extendida', 'blue');
                    var m = document.getElementById('protocolo-modal');
                    if (m) m.remove();
                    var c = document.getElementById('hunts-container');
                    if (c && typeof htmx !== 'undefined') htmx.trigger(c, 'reload-hunts');
                })
                .catch(function() { showToast('Error de red', 'red'); });
        }

        /* --- Mostrar modal protocolo (WS: protocolo:huntId|item|hunter) --- */
        function showProtocolo(huntId, item, hunter) {
            var old = document.getElementById('protocolo-modal');
            if (old) old.remove();
            var overlay = document.createElement('div');
            overlay.id = 'protocolo-modal';
            overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.65);display:flex;align-items:center;justify-content:center;';
            var hunterLine = hunter ? '<p style="color:#fca5a5;font-size:.78rem;margin-top:4px;">Hunter: <strong>' + hunter + '</strong></p>' : '';
            overlay.innerHTML = '<div style="background:#991b1b;border:3px solid #fca5a5;border-radius:20px;padding:28px 32px;max-width:360px;width:90%;text-align:center;"><div style="font-size:3rem;">&#x1F6A8;</div><p style="color:#fef2f2;font-size:1.15rem;font-weight:900;margin:10px 0 4px;">APLICA PROTOCOLO DE QUIEBRE</p><p style="color:#fca5a5;font-size:.85rem;font-weight:700;">' + item + '</p>' + hunterLine + '<div style="display:flex;gap:12px;margin-top:20px;justify-content:center;"><button onclick="mantenerBusqueda(' + huntId + ')" style="background:#fef2f2;color:#991b1b;font-weight:900;font-size:.8rem;border:none;border-radius:10px;padding:10px 20px;cursor:pointer;">Mantener Busqueda</button><button onclick="confirmarProtocolo(' + huntId + ')" style="background:#fca5a5;color:#7f1d1d;font-weight:900;font-size:.8rem;border:none;border-radius:10px;padding:10px 20px;cursor:pointer;">Confirmar Protocolo</button></div></div>';
            document.body.appendChild(overlay);
            setTimeout(function() { if (overlay.parentNode) overlay.remove(); }, 30000);
        }

        /* --- Aviso protocolo picker (WS: aviso-protocolo:huntId|item|picker) --- */
        function showAvisoProtocolo(huntId, item, picker) {
            var old = document.getElementById('aviso-protocolo-modal');
            if (old) old.remove();
            var overlay = document.createElement('div');
            overlay.id = 'aviso-protocolo-modal';
            overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;';
            var box = document.createElement('div');
            box.style.cssText = 'background:#78350f;border:2px solid #fbbf24;border-radius:16px;padding:24px 28px;max-width:340px;width:90%;text-align:center;';
            var icon = document.createElement('div');
            icon.style.cssText = 'font-size:2rem;margin-bottom:8px;';
            icon.textContent = '\u26A0\uFE0F';
            var p1 = document.createElement('p');
            p1.style.cssText = 'color:#fef3c7;font-size:1rem;font-weight:900;margin:0 0 4px;';
            p1.textContent = 'AVISO: El picker aplico protocolo';
            var p2 = document.createElement('p');
            p2.style.cssText = 'color:#fcd34d;font-size:.85rem;font-weight:700;margin:4px 0 2px;';
            p2.textContent = item;
            var p3 = document.createElement('p');
            p3.style.cssText = 'color:#fcd34d;font-size:.78rem;margin:0 0 16px;';
            p3.textContent = 'Reportado por: ' + picker;
            /* Botones */
            var row = document.createElement('div');
            row.style.cssText = 'display:flex;gap:10px;justify-content:center;';
            var btnMantener = document.createElement('button');
            btnMantener.style.cssText = 'flex:1;background:#fef3c7;color:#78350f;font-weight:900;font-size:.8rem;border:none;border-radius:8px;padding:10px 12px;cursor:pointer;';
            btnMantener.textContent = 'Mantener';
            btnMantener.onclick = function() { overlay.remove(); };
            var btnEntendido = document.createElement('button');
            btnEntendido.style.cssText = 'flex:1;background:#dc2626;color:white;font-weight:900;font-size:.8rem;border:none;border-radius:8px;padding:10px 12px;cursor:pointer;';
            btnEntendido.textContent = 'Entendido';
            btnEntendido.onclick = function() {
                fetch('/api/hunts/' + huntId + '/entendido', { method: 'POST' })
                    .then(function(r) { return r.json(); })
                    .then(function(d) {
                        showToast(d.ok ? 'Protocolo confirmado' : (d.detail || 'Error'), d.ok ? 'green' : 'red');
                        overlay.remove();
                    })
                    .catch(function() { showToast('Error de red', 'red'); overlay.remove(); });
            };
            row.appendChild(btnMantener);
            row.appendChild(btnEntendido);
            box.appendChild(icon);
            box.appendChild(p1);
            box.appendChild(p2);
            box.appendChild(p3);
            box.appendChild(row);
            overlay.appendChild(box);
            document.body.appendChild(overlay);
            setTimeout(function() { if (overlay.parentNode) overlay.remove(); }, 30000);
        }

        /* --- Protocolo libre (sin hunter asignado) --- */
        function showProtocoloLibre(huntId, item) { showProtocolo(huntId, item, null); }
        function showProtocoloConfirmar(huntId, item, hunter) { showProtocolo(huntId, item, hunter); }

        /* --- Broadcast (supervisor) --- */
        function sendBroadcast() {
            var ta = document.getElementById('broadcast-text');
            if (!ta || !ta.value.trim()) return;
            fetch('/api/broadcast-banner', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({message: ta.value}) })
                .then(function(r) { return r.json(); })
                .then(function(d) { showToast(d.message || 'Enviado a todos', 'green'); ta.value = ''; })
                .catch(function() { showToast('Error', 'red'); });
        }
        function sendHunterBroadcast() {
            var ta = document.getElementById('hunter-broadcast-text');
            if (!ta || !ta.value.trim()) return;
            fetch('/api/broadcast-hunter', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({message: ta.value}) })
                .then(function(r) { return r.json(); })
                .then(function(d) { showToast(d.message || 'Enviado al equipo', 'blue'); ta.value = ''; })
                .catch(function() { showToast('Error', 'red'); });
        }

        /* --- Feed edit (supervisor) --- */
        function feedEditStart(id) {
            var rd = document.getElementById('feed-read-' + id);
            var wr = document.getElementById('feed-write-' + id);
            if (rd) rd.classList.add('hidden');
            if (wr) wr.classList.remove('hidden');
        }
        function feedEditSave(id) {
            var ta = document.getElementById('feed-ta-' + id);
            if (!ta) return;
            fetch('/api/feed/' + id, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({message: ta.value}) })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    showToast('Guardado', 'green');
                    var fc = document.getElementById('feed-container');
                    if (fc && typeof htmx !== 'undefined') htmx.trigger(fc, 'reload-feed');
                })
                .catch(function() { showToast('Error', 'red'); });
        }

        /* --- Camera / Gallery helpers --- */
        function triggerCamera(prefix) {
            prefix = prefix || '';
            var g = document.getElementById(prefix + 'gallery-only-input');
            var c = document.getElementById(prefix + 'camera-only-input');
            if (g) g.value = '';
            if (c) c.click();
        }
        function triggerGallery(prefix) {
            prefix = prefix || '';
            var c = document.getElementById(prefix + 'camera-only-input');
            var g = document.getElementById(prefix + 'gallery-only-input');
            if (c) c.value = '';
            if (g) g.click();
        }
        function clearPhotoInputs(input, prefix) {
            prefix = prefix || '';
            var c = document.getElementById(prefix + 'camera-only-input');
            var g = document.getElementById(prefix + 'gallery-only-input');
            var prev = document.getElementById(prefix + 'photo-preview');
            if (c) c.value = '';
            if (g) g.value = '';
            if (prev) prev.classList.add('hidden');
        }
        function previewPhotoSelected(input, prefix) {
            prefix = prefix || '';
            if (!input.files || !input.files[0]) return;
            var reader = new FileReader();
            var prev = document.getElementById(prefix + 'photo-preview');
            var wrap = document.getElementById(prefix + 'photo-preview-container');
            reader.onload = function(e) {
                if (prev) { prev.src = e.target.result; prev.classList.remove('hidden'); }
                if (wrap) wrap.classList.remove('hidden');
            };
            reader.readAsDataURL(input.files[0]);
        }

        /* --- Sala Photo Modal (hunter: En sala con foto) --- */
        var _salaHuntId = null;
        function openSalaPhotoModal(huntId, itemName) {
            _salaHuntId = huntId;
            var nameEl = document.getElementById('sala-photo-item-name');
            if (nameEl) nameEl.textContent = itemName || '';
            clearSalaPhoto();
            var modal = document.getElementById('sala-photo-modal');
            if (modal) modal.classList.remove('hidden');
        }
        function closeSalaPhotoModal() {
            var modal = document.getElementById('sala-photo-modal');
            if (modal) modal.classList.add('hidden');
            clearSalaPhoto();
            _salaHuntId = null;
        }
        function previewSalaPhoto(input) {
            if (!input.files || !input.files[0]) return;
            // Auto-envio: mostrar estado cargando y disparar submit
            var btns    = document.getElementById('sala-photo-buttons');
            var state   = document.getElementById('sala-upload-state');
            var noBtn   = document.getElementById('sala-no-photo-btn');
            if (btns)  btns.classList.add('hidden');
            if (state) state.classList.remove('hidden');
            if (noBtn) noBtn.classList.add('hidden');
            submitSalaFound(true);
        }
        function clearSalaPhoto() {
            var c = document.getElementById('sala-camera-only-input');
            var g = document.getElementById('sala-gallery-only-input');
            if (c) c.value = '';
            if (g) g.value = '';
            // Restablecer estado del modal (en caso de reuso)
            var btns  = document.getElementById('sala-photo-buttons');
            var state = document.getElementById('sala-upload-state');
            var noBtn = document.getElementById('sala-no-photo-btn');
            if (btns)  btns.classList.remove('hidden');
            if (state) state.classList.add('hidden');
            if (noBtn) noBtn.classList.remove('hidden');
        }
        function submitSalaFound(withPhoto) {
            if (!_salaHuntId) return;
            var btn = document.getElementById('sala-confirm-btn');
            if (btn) { btn.disabled = true; btn.textContent = 'Enviando...'; }
            var formData = new FormData();
            formData.append('found_location', 'sala');
            if (withPhoto) {
                var camInput = document.getElementById('sala-camera-only-input');
                var galInput = document.getElementById('sala-gallery-only-input');
                var photoFile = (camInput && camInput.files && camInput.files[0])
                    ? camInput.files[0]
                    : (galInput && galInput.files && galInput.files[0] ? galInput.files[0] : null);
                if (photoFile) formData.append('photo_camera', photoFile);
            }
            fetch('/api/hunts/' + _salaHuntId + '/found', { method: 'POST', body: formData })
                .then(function(r) { return r.text(); })
                .then(function(html) {
                    var c = document.getElementById('hunts-container');
                    if (c) { c.innerHTML = html; if (typeof htmx !== 'undefined') htmx.process(c); tickTimers(); }
                    closeSalaPhotoModal();
                    var fc = document.getElementById('feed-container');
                    if (fc && typeof htmx !== 'undefined') htmx.trigger(fc, 'reload-feed');
                })
                .catch(function(e) {
                    showToast('Error al enviar: ' + e.message, 'red');
                    clearSalaPhoto(); // restablecer modal para reintentar
                });
        }

        /* --- Found Notif Banner: sala (verde) y bodega (azul) --- */
        var _foundNotifTimer  = null;
        var _foundNotifPhoto  = null;  // URL foto sala
        var _foundNotifItem   = '';
        var _foundNotifHunter = '';

        var NOTIF_STYLES = {
            sala:   { bg: '#15803d', label: 'PRODUCTO EN SALA',   verBtn: '#15803d' },
            bodega: { bg: '#1d4ed8', label: 'DISPONIBLE EN CARRO NARANJA', verBtn: '#1d4ed8' }
        };

        function showFoundNotif(type, huntId, item, hunter) {
            _foundNotifItem   = item   || '';
            _foundNotifHunter = hunter || '';
            _foundNotifPhoto  = null;

            var style   = NOTIF_STYLES[type] || NOTIF_STYLES.sala;
            var banner  = document.getElementById('found-notif-banner');
            var inner   = document.getElementById('found-notif-inner');
            var label   = document.getElementById('found-notif-label');
            var itemEl  = document.getElementById('found-notif-item');
            var hunterEl= document.getElementById('found-notif-hunter');
            var verBtn  = document.getElementById('found-notif-ver-btn');
            var iconSala  = document.getElementById('found-notif-icon-sala');
            var iconBodega= document.getElementById('found-notif-icon-bodega');

            if (inner)    inner.style.background    = style.bg;
            if (label)    label.textContent         = style.label;
            if (itemEl)   itemEl.textContent        = item   || '';
            if (hunterEl) hunterEl.textContent      = hunter ? 'Hunter: ' + hunter : '';
            if (verBtn)   { verBtn.style.display = 'none'; verBtn.style.color = style.verBtn; }
            // Iconos
            if (iconSala)   iconSala.style.display   = type === 'sala'   ? 'block' : 'none';
            if (iconBodega) iconBodega.style.display  = type === 'bodega' ? 'block' : 'none';

            // Mostrar banner
            if (banner) banner.style.transform = 'translateY(0)';

            // Auto-cierre 20 seg
            if (_foundNotifTimer) clearTimeout(_foundNotifTimer);
            _foundNotifTimer = setTimeout(closeFoundNotif, 20000);

            // Si es sala, cargar foto en background
            if (type === 'sala' && huntId) {
                fetch('/api/hunts/' + huntId + '/location-photo')
                    .then(function(r) { return r.json(); })
                    .then(function(d) {
                        if (d.photo) {
                            _foundNotifPhoto = d.photo;
                            if (verBtn) verBtn.style.display = 'block';
                        }
                    })
                    .catch(function() {});
            }
        }

        function closeFoundNotif() {
            if (_foundNotifTimer) { clearTimeout(_foundNotifTimer); _foundNotifTimer = null; }
            var banner = document.getElementById('found-notif-banner');
            if (banner) banner.style.transform = 'translateY(-110%)';
        }

        /* Aliases para backward compat con WS handler y funciones existentes */
        function closeSalaNotif()  { closeFoundNotif(); }
        function showSalaFoundModal(huntId, item, hunter) { showFoundNotif('sala', huntId, item, hunter); }
        function closeSalaFoundModal() { closeFoundNotif(); closeSalaPhotoSheet(); }

        function openSalaPhotoSheet() {
            closeFoundNotif();
            var overlay      = document.getElementById('sala-photo-sheet-overlay');
            var sheet        = document.getElementById('sala-photo-sheet');
            var sheetItem    = document.getElementById('sala-sheet-item');
            var sheetHunter  = document.getElementById('sala-sheet-hunter');
            var sheetPhoto   = document.getElementById('sala-sheet-photo');
            var sheetLoading = document.getElementById('sala-sheet-loading');
            if (sheetItem)   sheetItem.textContent   = _foundNotifItem;
            if (sheetHunter) sheetHunter.textContent = _foundNotifHunter ? 'Hunter: ' + _foundNotifHunter : '';
            if (sheetPhoto)   { sheetPhoto.src = '#'; sheetPhoto.style.display = 'none'; }
            if (sheetLoading) sheetLoading.style.display = 'flex';
            if (overlay) { overlay.style.pointerEvents = 'auto'; overlay.style.background = 'rgba(0,0,0,.6)'; }
            if (sheet)   sheet.style.transform = 'translateY(0)';
            if (_foundNotifPhoto && sheetPhoto) sheetPhoto.src = _foundNotifPhoto;
        }
        function closeSalaPhotoSheet() {
            var overlay = document.getElementById('sala-photo-sheet-overlay');
            var sheet   = document.getElementById('sala-photo-sheet');
            if (sheet)   sheet.style.transform = 'translateY(100%)';
            if (overlay) {
                overlay.style.background = 'rgba(0,0,0,0)';
                setTimeout(function() { overlay.style.pointerEvents = 'none'; }, 350);
            }
        }

        /* --- Photo lightbox --- */
        function showPhotoLightBox(src) {
            var lb = document.createElement('div');
            lb.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;cursor:zoom-out;';
            lb.innerHTML = '<img src="' + src + '" style="max-width:95vw;max-height:90vh;border-radius:12px;">';
            lb.onclick = function() { lb.remove(); };
            document.body.appendChild(lb);
        }
        function showQR(text) { alert('QR: ' + text); }

        /* --- Fill login helper --- */
        function fillLogin(user, pass) {
            var u = document.getElementById('username');
            var p = document.getElementById('password');
            if (u) u.value = user;
            if (p) p.value = pass;
        }

/* === WebSocket - Conexion en tiempo real (ES5) === */
(function() {
    var WS_USER = (function() {
        var b = document.body;
        return b ? (b.getAttribute('data-ws-user') || '') : '';
    })();
    if (!WS_USER) return; // Sin usuario, no conectar

    var socket = null;
    var reconnectTimer = null;

    function connectWS() {
        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsUrl = protocol + '//' + window.location.host + '/ws?user=' + encodeURIComponent(WS_USER);
        try {
            socket = new WebSocket(wsUrl);
        } catch(e) {
            scheduleReconnect();
            return;
        }

        socket.onopen = function() {
            var badge = document.getElementById('ws-status-badge');
            if (badge) {
                badge.textContent = 'Conectado';
                badge.className = 'bg-green-500 text-white text-[8px] font-black px-1.5 py-0.5 rounded uppercase tracking-wider animate-pulse';
            }
        };

        socket.onclose = function() {
            var badge = document.getElementById('ws-status-badge');
            if (badge) {
                badge.textContent = 'Desconectado';
                badge.className = 'bg-red-400 text-white text-[8px] font-black px-1.5 py-0.5 rounded uppercase tracking-wider';
            }
            scheduleReconnect();
        };

        socket.onerror = function() { scheduleReconnect(); };

        socket.onmessage = function(event) {
            var data = event.data;

            /* toast:color|mensaje */
            if (data.indexOf('toast:') === 0) {
                var parts = data.slice(6).split('|');
                var color = parts.length > 1 ? parts[0] : 'green';
                var msg   = parts.length > 1 ? parts.slice(1).join('|') : parts[0];
                if (typeof showToast === 'function') showToast(msg, color);
                return;
            }

            /* refresh - recargar hunts y feed */
            if (data === 'refresh') {
                var si = document.getElementById('search-input');
                var hc = document.getElementById('hunts-container');
                var fc = document.getElementById('feed-container');
                if (si && typeof htmx !== 'undefined') htmx.trigger(si, 'reload-hunts');
                else if (hc && typeof htmx !== 'undefined') htmx.trigger(hc, 'reload-hunts');
                if (fc && typeof htmx !== 'undefined') htmx.trigger(fc, 'reload-feed');
                /* Actualizar metricas */
                var ae = document.getElementById('metric-active-hunts');
                var fe2 = document.getElementById('metric-found');
                if (ae || fe2) {
                    fetch('/api/stats-json')
                        .then(function(r) { return r.json(); })
                        .then(function(d) {
                            if (ae) ae.textContent = d.active;
                            if (fe2) fe2.textContent = d.found;
                        });
                }
                return;
            }

            /* aviso-protocolo:huntId|item|picker */
            if (data.indexOf('aviso-protocolo:') === 0) {
                var payload = data.slice(16).split('|');
                var aHuntId = payload[0] || '';
                var aItem   = payload[1] || '';
                var aPicker = payload[2] || '';
                if (typeof showAvisoProtocolo === 'function') showAvisoProtocolo(aHuntId, aItem, aPicker);
                return;
            }

            /* protocolo:huntId|item|hunter */
            if (data.indexOf('protocolo:') === 0) {
                var parts2 = data.slice(10).split('|');
                var huntId = parts2[0] || '';
                var item2  = parts2[1] || '';
                var hunter = parts2[2] || null;
                if (typeof showProtocolo === 'function') showProtocolo(huntId, item2, hunter);
                return;
            }

            /* broadcast-banner:mensaje */
            if (data.indexOf('broadcast-banner:') === 0) {
                var msg2 = data.slice(17);
                if (typeof showPulseToast === 'function') showPulseToast(msg2, 'blue');
                return;
            }

            /* broadcast-hunter:huntId|mensaje */
            if (data.indexOf('broadcast-hunter:') === 0) {
                var bh = data.slice(17).split('|');
                var msg3 = bh.slice(1).join('|') || bh[0];
                if (typeof showPulseToast === 'function') showPulseToast(msg3, 'orange');
                return;
            }

            /* warn-hunter:huntId|item|qty */
            if (data.indexOf('warn-hunter:') === 0) {
                var wh = data.slice(12).split('|');
                var item3 = wh[1] || '';
                var qty   = wh[2] || '';
                if (typeof showPulseToast === 'function')
                    showPulseToast('Nueva alerta: ' + item3 + ' (' + qty + ' un.)', 'orange');
                return;
            }

            /* sala-photo:huntId|item|hunter */
            if (data.indexOf('sala-photo:') === 0) {
                var sp = data.slice(11).split('|');
                var spHuntId = sp[0] || '';
                var spItem   = sp[1] || '';
                var spHunter = sp[2] || '';
                if (typeof showFoundNotif === 'function') showFoundNotif('sala', spHuntId, spItem, spHunter);
                return;
            }

            /* bodega-found:huntId|item|hunter */
            if (data.indexOf('bodega-found:') === 0) {
                var bf = data.slice(13).split('|');
                var bfHuntId = bf[0] || '';
                var bfItem   = bf[1] || '';
                var bfHunter = bf[2] || '';
                if (typeof showFoundNotif === 'function') showFoundNotif('bodega', bfHuntId, bfItem, bfHunter);
                return;
            }

            /* extras-added:huntId|itemName|addedQty|totalQty|reporter */
            if (data.indexOf('extras-added:') === 0) {
                var ea = data.slice(13).split('|');
                var itemEa = ea[1] || '';
                var added  = ea[2] || '';
                var total  = ea[3] || '';
                var rep    = ea[4] || '';
                var msgEa  = '+' + added + ' un. adicionales  -  ' + itemEa + ' (total: ' + total + ' un.) Reportado por ' + rep;
                if (typeof showToast === 'function') showToast(msgEa, 'blue');
                /* Recargar hunts para ver cantidad actualizada */
                var hc2 = document.getElementById('hunts-container');
                if (hc2 && typeof htmx !== 'undefined') htmx.trigger(hc2, 'reload-hunts');
                return;
            }
        };
    }

    function scheduleReconnect() {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(connectWS, 3000);
    }

    /* Arrancar cuando DOM este listo */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', connectWS);
    } else {
        connectWS();
    }
})();
