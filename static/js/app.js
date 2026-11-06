/**
 * NoTI - Módulo de Gestión de Horarios y Turnos por Equipos
 * Controlador Frontend Interactivo con Soporte Drag & Drop y Menú Contextual
 */

function getISOWeekAndYear(d = new Date()) {
    const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    const dayNum = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
    const weekNo = Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
    return { week: weekNo, year: date.getUTCFullYear() };
}

function getISOWeeksInYear(year) {
    // En ISO 8601, el 28 de diciembre siempre pertenece a la última semana del año (52 o 53)
    const d = new Date(Date.UTC(year, 11, 28));
    return getISOWeekAndYear(d).week;
}

function getTodayDateStr() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

const initialCalendar = getISOWeekAndYear();
const STATE = {
    currentUser: null,
    currentTeamId: 1,
    currentYear: initialCalendar.year,
    currentWeek: initialCalendar.week,
    activeShiftBlock: null,
    gridData: null,
    catalog: [],
    draggedBlockId: null,
    pickerTarget: null // { userId, dateStr, canEdit, dayAssignments }
};

// --- Inicialización (Al iniciar sesión o refrescar la web) ---
document.addEventListener('DOMContentLoaded', async () => {
    // Asegurar que al ingresar o refrescar se posicione en la semana y año actual
    const current = getISOWeekAndYear();
    STATE.currentYear = current.year;
    STATE.currentWeek = current.week;

    await initAuth();
    await initTeams();
    await loadCatalog();
    await loadGrid();
    setupEventListeners();
});

// --- Autenticación y Sesión Formal ---
async function initAuth() {
    try {
        const curRes = await fetch('/api/auth/current-user');
        if (!curRes.ok) {
            window.location.href = '/login';
            return;
        }
        STATE.currentUser = await curRes.json();
        updateUserUI();

        if (STATE.currentUser.username === 'admin') {
            const navUsers = document.getElementById('nav-users-link');
            if (navUsers) navUsers.classList.remove('hidden');
        }
    } catch (err) {
        window.location.href = '/login';
    }
}

async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/login';
}

function updateUserUI() {
    if (!STATE.currentUser) return;
    document.getElementById('active-user-name').textContent = STATE.currentUser.name;
    document.getElementById('active-user-avatar').src = STATE.currentUser.avatar_url || 'https://via.placeholder.com/40';

    const currentRole = getCurrentUserRole();
    const roleBadge = document.getElementById('active-user-role-badge');
    roleBadge.textContent = `Rol: ${currentRole}`;
    roleBadge.className = `text-[10px] font-bold ${
        currentRole === 'ADMIN' ? 'text-blue-600' :
        currentRole === 'SCHEDULER' ? 'text-emerald-600' : 'text-slate-500'
    }`;
}

function getCurrentUserRole() {
    if (!STATE.currentUser || !STATE.currentUser.roles_by_team) return 'VISITANTE';
    return STATE.currentUser.roles_by_team[STATE.currentTeamId] || 'VISITANTE';
}

// --- Equipos ---
async function initTeams() {
    try {
        const res = await fetch('/api/teams');
        const teams = await res.json();
        const selector = document.getElementById('team-selector');
        selector.innerHTML = teams.map(t => `<option value="${t.id}">${t.name}</option>`).join('');

        if (teams.length > 0) {
            STATE.currentTeamId = teams[0].id;
        }

        selector.addEventListener('change', async (e) => {
            STATE.currentTeamId = parseInt(e.target.value);
            updateUserUI();
            await loadGrid();
        });
    } catch (err) {
        console.error('Error cargando equipos:', err);
    }
}

// --- Catálogo de Turnos (HU-02) con Drag & Drop ---
async function loadCatalog() {
    try {
        const res = await fetch('/api/catalog');
        STATE.catalog = await res.json();

        const palette = document.getElementById('shift-palette');
        palette.innerHTML = STATE.catalog.map(b => `
            <div draggable="true"
                ondragstart="handleDragStart(event, ${b.id})"
                onclick="selectShiftBrush(${b.id})"
                id="brush-btn-${b.id}"
                class="brush-btn px-3 py-1.5 rounded-xl border text-xs font-bold flex items-center gap-1.5 transition-all shadow-2xs hover:scale-105 cursor-grab active:cursor-grabbing select-none"
                style="border-color: ${b.color}; color: ${b.color}; background-color: ${b.color}15;"
                title="Arrastra este bloque a una celda o haz clic para usar como pincel">
                <i class="ph-bold ph-dots-six-vertical opacity-60"></i>
                <span class="w-3 h-3 rounded-full" style="background-color: ${b.color};"></span>
                <span>${b.name}</span>
                <span class="text-[9px] opacity-75 font-normal">(${b.duration_type === 'FULL_DAY' ? '1D' : '½D'})</span>
            </div>
        `).join('');

        if (STATE.catalog.length > 0 && !STATE.activeShiftBlock) {
            selectShiftBrush(STATE.catalog[0].id);
        }
    } catch (err) {
        console.error('Error cargando catálogo:', err);
    }
}

function selectShiftBrush(blockId) {
    STATE.activeShiftBlock = STATE.catalog.find(b => b.id === blockId);
    document.querySelectorAll('.brush-btn').forEach(btn => {
        btn.classList.remove('ring-2', 'ring-offset-2', 'ring-emerald-500', 'font-black');
    });

    const activeBtn = document.getElementById(`brush-btn-${blockId}`);
    if (activeBtn) {
        activeBtn.classList.add('ring-2', 'ring-offset-2', 'ring-emerald-500', 'font-black');
    }
}

// --- Drag & Drop Handlers ---
function handleDragStart(e, blockId) {
    STATE.draggedBlockId = blockId;
    e.dataTransfer.setData('text/plain', blockId.toString());
    e.dataTransfer.effectAllowed = 'copy';
}

function handleCellDragOver(e, canEdit, dateStr) {
    if (!canEdit) return;
    const todayStr = getTodayDateStr();
    if (dateStr && dateStr < todayStr && getCurrentUserRole() !== 'ADMIN') return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    const target = e.currentTarget;
    target.classList.add('ring-2', 'ring-emerald-500', 'bg-emerald-50/60');
}

function handleCellDragLeave(e) {
    e.currentTarget.classList.remove('ring-2', 'ring-emerald-500', 'bg-emerald-50/60');
}

async function handleCellDrop(e, userId, dateStr, canEdit) {
    e.preventDefault();
    e.currentTarget.classList.remove('ring-2', 'ring-emerald-500', 'bg-emerald-50/60');

    const todayStr = getTodayDateStr();
    if (dateStr < todayStr && getCurrentUserRole() !== 'ADMIN') {
        showToast('No se pueden agregar o modificar turnos en días anteriores a la fecha actual.', 'warning');
        return;
    }

    if (!canEdit) {
        showToast('No tienes permisos de edición sobre esta celda.', 'warning');
        return;
    }

    const blockIdStr = e.dataTransfer.getData('text/plain') || STATE.draggedBlockId;
    const blockId = parseInt(blockIdStr);
    if (!blockId) return;

    const block = STATE.catalog.find(b => b.id === blockId);
    if (!block) return;

    const period = (block.duration_type === 'HALF_DAY') ? 'FIRST_HALF' : 'FULL_DAY';
    await executeShiftAssignment(userId, dateStr, block.id, period);
}

// --- Carga y Renderizado de la Grilla Semanal (HU-04, HU-06, HU-07) ---
let currentGridAbortController = null;

async function loadGrid() {
    if (currentGridAbortController) {
        currentGridAbortController.abort();
    }
    currentGridAbortController = new AbortController();
    const signal = currentGridAbortController.signal;

    const gridContainer = document.getElementById('grid-container');
    if (gridContainer) {
        gridContainer.classList.add('opacity-50');
    }

    try {
        const headers = {};
        if (STATE.currentUser && STATE.currentUser.id) {
            headers['X-User-Id'] = String(STATE.currentUser.id);
        }
        const res = await fetch(`/api/schedules/grid?team_id=${STATE.currentTeamId}&year=${STATE.currentYear}&week=${STATE.currentWeek}`, {
            headers: headers,
            signal: signal
        });
        if (!res.ok) {
            let errorMsg = 'Error al obtener datos de la grilla';
            try {
                const errData = await res.json();
                if (errData && errData.detail) errorMsg = errData.detail;
            } catch (_) {
                const txt = await res.text();
                if (txt) errorMsg = txt;
            }
            console.error('Error al obtener datos de la grilla:', res.status, errorMsg);
            showToast(errorMsg, 'warning');

            // Revertir selector si la grilla previa existe para mantener coherencia UI
            if (STATE.gridData && STATE.gridData.schedule) {
                STATE.currentWeek = STATE.gridData.schedule.week_number;
                STATE.currentYear = STATE.gridData.schedule.year;
                renderGrid();
            }
            return;
        }
        STATE.gridData = await res.json();
        renderGrid();
        renderActionButtons();
    } catch (err) {
        if (err.name === 'AbortError') {
            // Cancelación intencional por navegación rápida; ignorar
            return;
        }
        console.error('Error cargando grilla:', err);
        showToast('Fallo de conexión al cargar la semana', 'warning');
        if (STATE.gridData && STATE.gridData.schedule) {
            STATE.currentWeek = STATE.gridData.schedule.week_number;
            STATE.currentYear = STATE.gridData.schedule.year;
            renderGrid();
        }
    } finally {
        if (currentGridAbortController && currentGridAbortController.signal === signal) {
            if (gridContainer) {
                gridContainer.classList.remove('opacity-50');
            }
        }
    }
}

function renderGrid() {
    const data = STATE.gridData;
    if (!data || !data.schedule) return;

    // Actualizar badge de estado general
    const statusBadge = document.getElementById('schedule-status-badge');
    const isConsolidated = (data.schedule.status === 'CONSOLIDATED');
    const isDraft = (data.schedule.status === 'DRAFT');
    statusBadge.textContent = isConsolidated ? 'Publicada' : (isDraft ? 'En borrador' : data.schedule.status);
    statusBadge.className = `px-2.5 py-0.5 text-xs font-bold rounded-full ${
        isConsolidated ? 'bg-emerald-100 text-emerald-800' :
        data.schedule.status === 'PUBLISHED' ? 'bg-blue-100 text-blue-800' : 'bg-slate-100 text-slate-700'
    }`;

    // Rango de fechas visible de la semana actual con indicación de semana y año
    if (data.dates && data.dates.length === 7) {
        const startDate = data.dates[0];
        const endDate = data.dates[6];
        const [sY, sM, sD] = startDate.split('-');
        const [eY, eM, eD] = endDate.split('-');
        document.getElementById('current-week-label').textContent = `Semana ${data.schedule.week_number} - ${data.schedule.year} (${sD}/${sM} - ${eD}/${eM}/${eY})`;
    } else {
        document.getElementById('current-week-label').textContent = `Semana ${data.schedule.week_number} - ${data.schedule.year}`;
    }

    // Cabeceras de días con fechas y resaltado de "Hoy"
    const todayStr = getTodayDateStr();
    const dayNames = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
    data.dates.forEach((dateStr, idx) => {
        const th = document.getElementById(`th-day-${idx}`);
        const parts = dateStr.split('-');
        const isToday = (dateStr === todayStr);
        th.className = `py-3 px-3 text-center border-r border-slate-200 transition-colors ${isToday ? 'bg-emerald-50/90 font-extrabold text-emerald-900 border-b-2 border-b-emerald-600' : ''}`;
        th.innerHTML = `
            <div class="flex items-center justify-center gap-1.5">
                <span>${dayNames[idx]}</span>
                ${isToday ? '<span class="bg-emerald-600 text-white text-[9px] px-1.5 py-0.5 rounded-full font-extrabold uppercase shadow-2xs">Hoy</span>' : ''}
            </div>
            <div class="text-[10px] font-normal ${isToday ? 'text-emerald-700 font-bold' : 'text-slate-500'}">${parts[2]}/${parts[1]}</div>
        `;
    });

    // Fila de Indicadores de Cupos Diarios (HU-03)
    const quotaRow = document.getElementById('quota-indicator-row');
    let quotaCellsHtml = `<td class="py-2 px-4 font-bold border-r border-slate-200 text-slate-700 flex items-center gap-1.5"><i class="ph-bold ph-shield-check text-emerald-600 text-sm"></i> Topes Diarios:</td>`;

    data.dates.forEach(dateStr => {
        const dayQuota = data.quotas[dateStr];
        const remoteInfo = dayQuota ? dayQuota.quotas["REMOTO"] : null;

        if (remoteInfo && remoteInfo.limit !== null) {
            const isFull = remoteInfo.is_full;
            quotaCellsHtml += `
                <td class="py-1 px-2 text-center border-r border-slate-200">
                    <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        isFull ? 'bg-rose-100 text-rose-700 border border-rose-300' : 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                    }">
                        REMOTO: ${remoteInfo.used}/${remoteInfo.limit}
                        ${isFull ? '<i class="ph-bold ph-prohibit text-rose-600"></i>' : '<i class="ph-bold ph-check text-emerald-600"></i>'}
                    </span>
                </td>
            `;
        } else {
            quotaCellsHtml += `<td class="py-1 px-2 text-center border-r border-slate-200 text-[10px] text-slate-400">Sin límite</td>`;
        }
    });
    quotaRow.innerHTML = quotaCellsHtml;

    // Renderizar Filas Exclusivas para miembros SCHEDULER (HU-04, RN-01)
    const tbody = document.getElementById('grid-body');
    if (!data.rows || data.rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-slate-400">No hay miembros SCHEDULER configurados en este equipo.</td></tr>`;
        return;
    }

    const currentRole = getCurrentUserRole();
    const canEditGeneral = (currentRole === 'ADMIN');

    tbody.innerHTML = data.rows.map(row => {
        const isMyRow = (STATE.currentUser && parseInt(STATE.currentUser.id) === parseInt(row.user.id));
        const isLockedState = ['CONFIRMED', 'SUBMITTED'].includes(row.status.workflow_state);
        const canEditThisRow = canEditGeneral || (isMyRow && !isLockedState);

        // Indicador Visual de Confirmación (HU-06) o Rechazo (HU-07)
        let statusBadgeHtml = '';
        if (row.status.workflow_state === 'CONFIRMED') {
            statusBadgeHtml = `
                <div class="flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-100 border border-emerald-300 text-emerald-800 text-[10px] font-bold shadow-2xs check-pulse" title="${row.status.feedback_comment || 'Semana confirmada formalmente'}">
                    <i class="ph-fill ph-check-circle text-emerald-600 text-sm"></i>
                    <span>Confirmado</span>
                </div>
            `;
        } else if (row.status.workflow_state === 'REJECTED') {
            statusBadgeHtml = `
                <div class="flex items-center gap-1 px-2 py-0.5 rounded-full bg-rose-100 border border-rose-300 text-rose-800 text-[10px] font-bold" title="Rechazado: ${row.status.feedback_comment || ''}">
                    <i class="ph-fill ph-warning-circle text-rose-600 text-sm"></i>
                    <span>Rechazado</span>
                </div>
            `;
        } else if (row.status.workflow_state === 'SUBMITTED') {
            statusBadgeHtml = `
                <div class="flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 text-[10px] font-semibold">
                    <i class="ph-bold ph-paper-plane-tilt"></i>
                    <span>Publicado</span>
                </div>
            `;
        } else {
            statusBadgeHtml = `
                <div class="flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 text-[10px] font-medium">
                    <i class="ph-bold ph-clock"></i>
                    <span>Borrador</span>
                </div>
            `;
        }

        // Celdas de turnos para cada día (con soporte Drag & Drop, Pincel y Click)
        let cellsHtml = '';
        const isPrivateDraftRow = (!isMyRow && row.status.workflow_state === 'DRAFT' && !canEditGeneral);

        data.dates.forEach(dateStr => {
            const isPast = (dateStr < todayStr);
            const canEditCell = canEditThisRow && (!isPast || canEditGeneral);
            const dayAssignments = row.assignments.filter(a => a.date === dateStr);
            let cellContent = '';

            if (isPrivateDraftRow) {
                cellContent = `
                    <div class="w-full h-12 rounded-lg bg-slate-50/60 border border-dashed border-slate-200 flex items-center justify-center text-slate-300 text-[11px] italic select-none"
                         title="Borrador privado del colaborador">
                        <i class="ph ph-clock-countdown mr-1 opacity-60"></i> Borrador
                    </div>
                `;
            } else if (isPast && !canEditCell) {
                if (dayAssignments.length === 0) {
                    cellContent = `
                        <div class="w-full h-12 rounded-lg bg-slate-100/70 border border-slate-200/80 flex items-center justify-center text-slate-400 cursor-not-allowed text-xs shadow-2xs"
                             title="Día pasado (no se pueden agregar turnos)">
                            <i class="ph-bold ph-lock-simple text-slate-400 text-sm"></i>
                        </div>
                    `;
                } else if (dayAssignments.length === 1 && dayAssignments[0].period === 'FULL_DAY') {
                    const a = dayAssignments[0];
                    cellContent = `
                        <div class="w-full h-12 rounded-xl text-white font-bold text-xs p-2 flex flex-col justify-center items-center text-center shadow-xs cursor-default relative opacity-90"
                             style="background-color: ${a.shift_block.color};"
                             title="Turno en día pasado">
                            <span class="truncate w-full">${a.shift_block.name}</span>
                            <span class="absolute top-1 left-1.5 text-white/90 drop-shadow" title="Día pasado (cerrado)">
                                <i class="ph-bold ph-lock-simple text-xs"></i>
                            </span>
                        </div>
                    `;
                } else {
                    const firstHalf = dayAssignments.find(a => a.period === 'FIRST_HALF');
                    const secondHalf = dayAssignments.find(a => a.period === 'SECOND_HALF');
                    cellContent = `
                        <div class="half-day-split rounded-xl overflow-hidden border border-slate-200 opacity-90">
                            <div class="half-day-part text-white cursor-default relative"
                                style="background-color: ${firstHalf ? firstHalf.shift_block.color : '#e2e8f0'}; color: ${firstHalf ? '#ffffff' : '#64748b'};"
                                title="Mañana (día pasado)">
                                <span class="truncate">${firstHalf ? firstHalf.shift_block.name + ' (M)' : '-'}</span>
                                ${firstHalf ? '<span class="absolute top-0.5 left-1 text-white/90 drop-shadow text-[9px]"><i class="ph-bold ph-lock-simple"></i></span>' : ''}
                            </div>
                            <div class="half-day-part text-white cursor-default relative"
                                style="background-color: ${secondHalf ? secondHalf.shift_block.color : '#cbd5e1'}; color: ${secondHalf ? '#ffffff' : '#475569'};"
                                title="Tarde (día pasado)">
                                <span class="truncate">${secondHalf ? secondHalf.shift_block.name + ' (T)' : '-'}</span>
                                ${secondHalf ? '<span class="absolute top-0.5 left-1 text-white/90 drop-shadow text-[9px]"><i class="ph-bold ph-lock-simple"></i></span>' : ''}
                            </div>
                        </div>
                    `;
                }
            } else if (dayAssignments.length === 0) {
                cellContent = `
                    <div onclick="handleCellClick(${row.user.id}, '${dateStr}', ${canEditCell})"
                        ondragover="handleCellDragOver(event, ${canEditCell}, '${dateStr}')"
                        ondragleave="handleCellDragLeave(event)"
                        ondrop="handleCellDrop(event, ${row.user.id}, '${dateStr}', ${canEditCell})"
                        class="w-full h-12 rounded-lg border-2 border-dashed border-slate-200 hover:border-emerald-400 flex items-center justify-center text-slate-300 hover:text-emerald-600 cursor-pointer transition-all text-xs font-bold hover:bg-emerald-50/30 group shadow-2xs"
                        title="${canEditCell ? (STATE.activeShiftBlock ? 'Clic para asignar ' + STATE.activeShiftBlock.name : 'Clic para seleccionar turno') : 'Solo lectura'}">
                        <span class="group-hover:scale-125 transition-transform">${canEditCell ? '+' : ''}</span>
                    </div>
                `;
            } else if (dayAssignments.length === 1 && dayAssignments[0].period === 'FULL_DAY') {
                const a = dayAssignments[0];
                const isDraft = (a.is_draft && data.schedule.status !== 'CONSOLIDATED');
                cellContent = `
                    <div onclick="handleCellClick(${row.user.id}, '${dateStr}', ${canEditCell})"
                        ondragover="handleCellDragOver(event, ${canEditCell}, '${dateStr}')"
                        ondragleave="handleCellDragLeave(event)"
                        ondrop="handleCellDrop(event, ${row.user.id}, '${dateStr}', ${canEditCell})"
                        oncontextmenu="handleCellRightClick(event, ${a.id}, ${canEditCell})"
                        class="w-full h-12 rounded-xl text-white font-bold text-xs p-2 flex flex-col justify-center items-center text-center shadow-xs cursor-pointer cell-shift-btn relative group transition-all"
                        style="background-color: ${a.shift_block.color};"
                        title="${canEditCell ? 'Clic para reasignar bloque con pincel o clic derecho para eliminar' : ''}">
                        <span class="truncate w-full">${a.shift_block.name}</span>
                        
                        <!-- Ícono de Candado: Abierto en Borrador / Cerrado en Definitivo -->
                        <span class="absolute top-1 left-1.5 text-white/90 drop-shadow" title="${isDraft ? 'Borrador (editable)' : 'Publicado y consolidado'}">
                            <i class="ph-bold ${isDraft ? 'ph-lock-simple-open' : 'ph-lock-simple'} text-xs"></i>
                        </span>

                        ${canEditCell ? '<button onclick="event.stopPropagation(); deleteAssignment(' + a.id + ')" class="absolute top-1 right-1 opacity-0 group-hover:opacity-100 text-white/80 hover:text-white p-0.5 text-[10px] bg-black/25 rounded transition-opacity" title="Eliminar turno"><i class="ph-bold ph-x"></i></button>' : ''}
                    </div>
                `;
            } else {
                // Media Jornada (HALF_DAY)
                const firstHalf = dayAssignments.find(a => a.period === 'FIRST_HALF');
                const secondHalf = dayAssignments.find(a => a.period === 'SECOND_HALF');

                const isDraftM = firstHalf && firstHalf.is_draft && data.schedule.status !== 'CONSOLIDATED';
                const isDraftT = secondHalf && secondHalf.is_draft && data.schedule.status !== 'CONSOLIDATED';

                cellContent = `
                    <div class="half-day-split rounded-xl overflow-hidden border border-slate-200"
                        ondragover="handleCellDragOver(event, ${canEditCell}, '${dateStr}')"
                        ondragleave="handleCellDragLeave(event)"
                        ondrop="handleCellDrop(event, ${row.user.id}, '${dateStr}', ${canEditCell})">
                        <div onclick="handleHalfCellClick(${row.user.id}, '${dateStr}', 'FIRST_HALF', ${canEditCell})"
                            class="half-day-part text-white cursor-pointer relative group"
                            style="background-color: ${firstHalf ? firstHalf.shift_block.color : '#e2e8f0'}; color: ${firstHalf ? '#ffffff' : '#64748b'};"
                            title="Mañana">
                            <span class="truncate">${firstHalf ? firstHalf.shift_block.name + ' (M)' : 'Mañana +'}</span>
                            ${firstHalf ? `<span class="absolute top-0.5 left-1 text-white/90 drop-shadow text-[9px]"><i class="ph-bold ${isDraftM ? 'ph-lock-simple-open' : 'ph-lock-simple'}"></i></span>` : ''}
                            ${firstHalf && canEditCell ? '<button onclick="event.stopPropagation(); deleteAssignment(' + firstHalf.id + ')" class="absolute right-1 opacity-0 group-hover:opacity-100 text-white p-0.5"><i class="ph-bold ph-x"></i></button>' : ''}
                        </div>
                        <div onclick="handleHalfCellClick(${row.user.id}, '${dateStr}', 'SECOND_HALF', ${canEditCell})"
                            class="half-day-part text-white cursor-pointer relative group"
                            style="background-color: ${secondHalf ? secondHalf.shift_block.color : '#cbd5e1'}; color: ${secondHalf ? '#ffffff' : '#475569'};"
                            title="Tarde">
                            <span class="truncate">${secondHalf ? secondHalf.shift_block.name + ' (T)' : 'Tarde +'}</span>
                            ${secondHalf ? `<span class="absolute top-0.5 left-1 text-white/90 drop-shadow text-[9px]"><i class="ph-bold ${isDraftT ? 'ph-lock-simple-open' : 'ph-lock-simple'}"></i></span>` : ''}
                            ${secondHalf && canEditCell ? '<button onclick="event.stopPropagation(); deleteAssignment(' + secondHalf.id + ')" class="absolute right-1 opacity-0 group-hover:opacity-100 text-white p-0.5"><i class="ph-bold ph-x"></i></button>' : ''}
                        </div>
                    </div>
                `;
            }

            cellsHtml += `<td class="p-2 border-r border-slate-200 align-middle">${cellContent}</td>`;
        });

        return `
            <tr class="hover:bg-slate-50/50 ${isMyRow ? 'bg-emerald-50/25' : ''}">
                <td class="p-3 border-r border-slate-200 align-middle">
                    <div class="flex items-center justify-between gap-2">
                        <div class="flex items-center gap-2.5">
                            <div class="relative">
                                <img src="${row.user.avatar_url || 'https://via.placeholder.com/40'}" class="w-9 h-9 rounded-full object-cover ring-2 ${isMyRow ? 'ring-emerald-500' : 'ring-slate-200'}">
                                ${row.status.workflow_state === 'CONFIRMED' ? '<span class="absolute -top-1 -right-1 bg-emerald-500 text-white rounded-full p-0.5 text-[10px]"><i class="ph-bold ph-check"></i></span>' : ''}
                            </div>
                            <div>
                                <div class="font-bold text-xs text-slate-900 flex items-center gap-1">
                                    ${row.user.name}
                                    ${isMyRow ? '<span class="text-[9px] bg-emerald-100 text-emerald-800 font-bold px-1.5 py-0.2 rounded">Tú</span>' : ''}
                                </div>
                                <div class="text-[10px] text-slate-500">${row.user.email}</div>
                            </div>
                        </div>
                        <div>${statusBadgeHtml}</div>
                    </div>
                    ${row.status.workflow_state === 'REJECTED' && row.status.feedback_comment ? `
                        <div class="mt-1.5 p-1.5 bg-rose-50 border border-rose-200 rounded text-[10px] text-rose-800">
                            <strong>Discrepancia:</strong> ${row.status.feedback_comment}
                        </div>
                    ` : ''}
                </td>
                ${cellsHtml}
            </tr>
        `;
    }).join('');
}

// --- Asignación al Hacer Clic: Pincel Activo o Selector Contextual ---
function handleCellClick(userId, dateStr, canEdit) {
    const todayStr = getTodayDateStr();
    if (dateStr < todayStr && getCurrentUserRole() !== 'ADMIN') {
        showToast('No se pueden agregar o modificar turnos en días anteriores a la fecha actual.', 'warning');
        return;
    }
    if (!canEdit) {
        showToast('No tienes permisos de edición sobre esta celda (o el horario ya fue confirmado).', 'warning');
        return;
    }

    if (STATE.activeShiftBlock) {
        // Asignación directa con el pincel activo (Requerimiento Clave)
        const block = STATE.activeShiftBlock;
        const period = (block.duration_type === 'HALF_DAY') ? 'FIRST_HALF' : 'FULL_DAY';
        executeShiftAssignment(userId, dateStr, block.id, period);
    } else {
        openCellPicker(userId, dateStr, canEdit);
    }
}

function handleHalfCellClick(userId, dateStr, period, canEdit) {
    const todayStr = getTodayDateStr();
    if (dateStr < todayStr && getCurrentUserRole() !== 'ADMIN') {
        showToast('No se pueden agregar o modificar turnos en días anteriores a la fecha actual.', 'warning');
        return;
    }
    if (!canEdit) return;

    if (STATE.activeShiftBlock) {
        executeShiftAssignment(userId, dateStr, STATE.activeShiftBlock.id, period);
    } else {
        openCellPicker(userId, dateStr, canEdit, period);
    }
}

function openCellPicker(userId, dateStr, canEdit, defaultPeriod = 'FULL_DAY') {
    const data = STATE.gridData;
    const row = data.rows.find(r => r.user.id === userId);
    const dayQuota = data.quotas[dateStr] || { day_name: '', quotas: {} };
    const dayAssignments = row ? row.assignments.filter(a => a.date === dateStr) : [];

    STATE.pickerTarget = {
        userId,
        dateStr,
        canEdit,
        dayAssignments
    };

    document.getElementById('picker-cell-subtitle').textContent = `Para ${row ? row.user.name : ''} - ${dayQuota.day_name} (${dateStr})`;

    const listContainer = document.getElementById('picker-blocks-list');
    listContainer.innerHTML = STATE.catalog.map(b => {
        const qInfo = dayQuota.quotas[b.name];
        const isFull = (qInfo && qInfo.is_full);
        const quotaLabel = (qInfo && qInfo.limit !== null)
            ? (isFull ? `<span class="text-[10px] font-bold text-rose-600 bg-rose-50 px-1.5 py-0.5 rounded border border-rose-200">Cupo Lleno (${qInfo.used}/${qInfo.limit})</span>`
                      : `<span class="text-[10px] font-medium text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded">Cupo: ${qInfo.used}/${qInfo.limit}</span>`)
            : `<span class="text-[10px] text-slate-400">Sin límite</span>`;

        if (b.duration_type === 'HALF_DAY') {
            return `
                <div class="p-2.5 rounded-xl border border-slate-200 hover:border-slate-300 bg-white flex items-center justify-between gap-2 shadow-2xs">
                    <div class="flex items-center gap-2">
                        <span class="w-4 h-4 rounded-full" style="background-color: ${b.color};"></span>
                        <div>
                            <div class="text-xs font-bold text-slate-900">${b.name}</div>
                            <div class="flex items-center gap-1.5 mt-0.5">
                                <span class="text-[10px] bg-amber-100 text-amber-800 font-semibold px-1.5 py-0.2 rounded">Media Jornada</span>
                                ${quotaLabel}
                            </div>
                        </div>
                    </div>
                    <div class="flex items-center gap-1">
                        <button onclick="pickBlockAndAssign(${b.id}, 'FIRST_HALF')" class="px-2 py-1 text-[11px] font-bold rounded-lg border border-slate-200 hover:bg-slate-100 text-slate-700">Mañana</button>
                        <button onclick="pickBlockAndAssign(${b.id}, 'SECOND_HALF')" class="px-2 py-1 text-[11px] font-bold rounded-lg border border-slate-200 hover:bg-slate-100 text-slate-700">Tarde</button>
                    </div>
                </div>
            `;
        } else {
            return `
                <div onclick="pickBlockAndAssign(${b.id}, 'FULL_DAY')" class="p-2.5 rounded-xl border border-slate-200 hover:border-emerald-400 hover:bg-emerald-50/20 bg-white flex items-center justify-between cursor-pointer shadow-2xs transition-all">
                    <div class="flex items-center gap-2">
                        <span class="w-4 h-4 rounded-full" style="background-color: ${b.color};"></span>
                        <div>
                            <div class="text-xs font-bold text-slate-900">${b.name}</div>
                            <div class="flex items-center gap-1.5 mt-0.5">
                                <span class="text-[10px] bg-blue-100 text-blue-800 font-semibold px-1.5 py-0.2 rounded">Día Completo</span>
                                ${quotaLabel}
                            </div>
                        </div>
                    </div>
                    <i class="ph-bold ph-plus-circle text-lg text-emerald-600"></i>
                </div>
            `;
        }
    }).join('');

    const btnClear = document.getElementById('btn-picker-clear');
    btnClear.style.display = (dayAssignments.length > 0) ? 'inline-flex' : 'none';

    openModal('modal-cell-picker');
}

async function pickBlockAndAssign(shiftBlockId, period) {
    if (!STATE.pickerTarget) return;
    const { userId, dateStr } = STATE.pickerTarget;
    closeModal('modal-cell-picker');
    await executeShiftAssignment(userId, dateStr, shiftBlockId, period);
}

async function clearCurrentCellAssignment() {
    if (!STATE.pickerTarget) return;
    const { dayAssignments } = STATE.pickerTarget;
    closeModal('modal-cell-picker');
    for (const a of dayAssignments) {
        await deleteAssignment(a.id);
    }
}

async function executeShiftAssignment(userId, dateStr, shiftBlockId, period) {
    const block = STATE.catalog.find(b => b.id === shiftBlockId);
    const myRow = STATE.gridData.rows ? STATE.gridData.rows.find(r => parseInt(r.user.id) === parseInt(userId)) : null;
    const isDraft = !myRow || myRow.status.workflow_state === 'DRAFT';

    // Validación en tiempo real en frontend: Solo cuando NO es borrador privado (en borrador no computa topes)
    const dayQuota = STATE.gridData.quotas[dateStr];
    if (!isDraft && dayQuota && dayQuota.quotas[block.name]) {
        const qInfo = dayQuota.quotas[block.name];
        if (qInfo.is_full) {
            // Verificar si el usuario ya tiene ese mismo bloque asignado en esa fecha
            const alreadyHasIt = myRow && myRow.assignments.some(a => a.date === dateStr && a.shift_block_id === shiftBlockId);
            if (!alreadyHasIt) {
                document.getElementById('quota-alert-message').innerHTML = `
                    El cupo diario para <strong>${block.name}</strong> el día <strong>${dayQuota.day_name} (${dateStr})</strong> está completo (${qInfo.used}/${qInfo.limit}).
                `;
                openModal('modal-quota-alert');
                return;
            }
        }
    }

    try {
        const opId = STATE.currentUser ? STATE.currentUser.id : '';
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/assign?operator_id=${opId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-User-Id': String(opId)
            },
            body: JSON.stringify({
                user_id: userId,
                date: dateStr,
                shift_block_id: shiftBlockId,
                period: period,
                is_draft: isDraft
            })
        });

        if (res.ok) {
            showToast(`Turno "${block.name}" guardado.`, 'success');
            await loadGrid();
        } else {
            const err = await res.json();
            document.getElementById('quota-alert-message').textContent = err.detail || 'Error al asignar turno';
            openModal('modal-quota-alert');
        }
    } catch (err) {
        console.error('Error al guardar asignación:', err);
    }
}

function handleCellRightClick(e, assignmentId, canEdit) {
    e.preventDefault();
    if (canEdit) deleteAssignment(assignmentId);
}

let targetAssignmentIdToDelete = null;

async function deleteAssignment(assignmentId) {
    const role = getCurrentUserRole();
    if (role === 'ADMIN' && STATE.gridData && STATE.gridData.rows) {
        const row = STATE.gridData.rows.find(r => r.assignments.some(a => a.id === assignmentId));
        if (row && row.status && row.status.workflow_state === 'CONFIRMED') {
            targetAssignmentIdToDelete = assignmentId;
            document.getElementById('admin-delete-user-name').textContent = row.user.name;
            openModal('modal-admin-delete-confirmed');
            return;
        }
    }
    await executeDeleteAssignment(assignmentId);
}

async function executeDeleteAssignment(assignmentId) {
    try {
        const opId = STATE.currentUser ? STATE.currentUser.id : '';
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/assign/${assignmentId}?operator_id=${opId}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            showToast('Asignación eliminada correctamente.', 'info');
            await loadGrid();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Error al eliminar asignación', 'warning');
        }
    } catch (err) {
        console.error(err);
    }
}

// --- Botones Contextuales de Acción ---
function renderActionButtons() {
    const container = document.getElementById('action-buttons-container');
    const role = getCurrentUserRole();
    const data = STATE.gridData;
    if (!data) return;

    let buttons = [];

    if (role === 'ADMIN') {
        const schedulers = data.rows || [];
        const unconfirmed = schedulers.filter(r => r.status.workflow_state !== 'CONFIRMED');
        const allConfirmed = schedulers.length > 0 && unconfirmed.length === 0;

        if (allConfirmed) {
            buttons.push(`
                <button onclick="handleAdminConsolidateClick()" class="px-3.5 py-1.5 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 rounded-xl shadow-xs flex items-center gap-1.5 transition-all cursor-pointer">
                    <i class="ph-bold ph-check-square"></i> Consolidar Horario
                </button>
            `);
        } else {
            const namesList = unconfirmed.map(r => r.user.name).join(', ');
            buttons.push(`
                <button disabled class="px-3 py-1.5 text-xs font-bold text-slate-400 bg-slate-100 rounded-xl border border-slate-200 flex items-center gap-1.5 cursor-not-allowed opacity-80" title="Pendiente de confirmación de: ${namesList}">
                    <i class="ph-bold ph-lock"></i> Consolidar Horario (Faltan: ${namesList || 'Sin miembros'})
                </button>
            `);
        }

        const rejectedMember = data.rows.find(r => r.status.workflow_state === 'REJECTED');
        if (rejectedMember) {
            buttons.push(`
                <button onclick="adminReopenSchedule(${rejectedMember.user.id})" class="px-3 py-1.5 text-xs font-bold text-white bg-amber-600 hover:bg-amber-700 rounded-xl shadow-xs flex items-center gap-1.5">
                    <i class="ph-bold ph-arrows-clockwise"></i> Reabrir Ajuste (${rejectedMember.user.name})
                </button>
            `);
        }
    } else if (role === 'SCHEDULER') {
        const myRow = data.rows ? data.rows.find(r => parseInt(r.user.id) === parseInt(STATE.currentUser.id)) : null;
        const myState = myRow ? myRow.status.workflow_state : 'DRAFT';
        const isConsolidated = (data.schedule.status === 'CONSOLIDATED');

        if (myState === 'DRAFT') {
            buttons.push(`
                <button onclick="publishProposal()" class="px-3.5 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl shadow-xs flex items-center gap-1.5 transition-all cursor-pointer">
                    <i class="ph-bold ph-paper-plane-tilt"></i> Publicar
                </button>
            `);
        } else if (myState === 'SUBMITTED') {
            buttons.push(`
                <button onclick="unpublishProposal()" class="px-3 py-1.5 text-xs font-bold text-slate-700 bg-white hover:bg-slate-50 border border-slate-300 rounded-xl shadow-xs flex items-center gap-1.5 transition-all cursor-pointer" title="Regresar a borrador para modificar turnos">
                    <i class="ph-bold ph-arrow-u-up-left"></i> Borrador
                </button>
            `);
            buttons.push(`
                <button onclick="handleSchedulerConfirmClick()" class="px-3.5 py-1.5 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 rounded-xl shadow-xs flex items-center gap-1.5 transition-all cursor-pointer">
                    <i class="ph-bold ph-check-circle"></i> Confirmar mi Horario
                </button>
            `);
        } else if (myState === 'CONFIRMED') {
            // Confirmado: ya no puede modificar turnos
        }

        // Opción de Rechazo: Disponible mientras la semana NO esté consolidada y el usuario NO esté confirmado
        // Requiere que al menos OTRO colaborador SCHEDULER del equipo se encuentre en PUBLICADO o CONFIRMADO
        const otherPublished = data.rows && data.rows.some(r =>
            parseInt(r.user.id) !== parseInt(STATE.currentUser.id) &&
            ['SUBMITTED', 'CONFIRMED'].includes(r.status.workflow_state)
        );

        if (!isConsolidated && myState !== 'CONFIRMED') {
            if (myState === 'REJECTED') {
                buttons.push(`
                    <button onclick="openModal('modal-revert')" class="px-3 py-1.5 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer">
                        <i class="ph-bold ph-arrow-counter-clockwise"></i> Revertir a Confirmado
                    </button>
                `);
            } else if (otherPublished) {
                buttons.push(`
                    <button onclick="openModal('modal-reject')" class="px-3 py-1.5 text-xs font-bold text-rose-600 hover:bg-rose-50 border border-rose-300 rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer transition-all">
                        <i class="ph-bold ph-x-circle"></i> Rechazar
                    </button>
                `);
            } else {
                buttons.push(`
                    <button disabled class="px-3 py-1.5 text-xs font-bold text-slate-400 bg-slate-100 border border-slate-200 rounded-xl shadow-2xs flex items-center gap-1.5 cursor-not-allowed opacity-60 pointer-events-none select-none" title="Opción deshabilitada: Requiere que al menos otro miembro del equipo haya publicado su propuesta para registrar un rechazo.">
                        <i class="ph-bold ph-x-circle"></i> Rechazar
                    </button>
                `);
            }
        }
    } else {
        buttons.push(`
            <span class="text-xs font-medium text-slate-500 bg-slate-100 px-3 py-1 rounded-lg border border-slate-200">
                <i class="ph ph-eye mr-1"></i> Modo Solo Lectura (Visitante)
            </span>
        `);
    }

    container.innerHTML = buttons.join('');
}

// --- Acciones de Workflow (HU-05, HU-06, HU-07) ---
async function publishProposal() {
    try {
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/publish`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: STATE.currentUser.id })
        });
        if (res.ok) {
            showToast('Propuesta publicada exitosamente. Ahora es visible para el equipo y computa en los topes.', 'success');
            await loadGrid();
        } else {
            const err = await res.json();
            document.getElementById('quota-alert-message').textContent = err.detail || 'Error al publicar propuesta';
            openModal('modal-quota-alert');
        }
    } catch (err) {
        console.error(err);
    }
}

async function unpublishProposal() {
    try {
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/unpublish`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: STATE.currentUser.id })
        });
        if (res.ok) {
            showToast('Horario regresado a Borrador. Ahora puedes modificar tus turnos.', 'info');
            await loadGrid();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Error al regresar a borrador', 'warning');
        }
    } catch (err) {
        console.error(err);
    }
}

// --- Validación de Días Laborales Vacíos (Lunes a Viernes) ---
const WORK_DAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"];

function getEmptyWorkDaysForUser(row) {
    if (!STATE.gridData || !STATE.gridData.dates || !row || !row.assignments) return [];
    const workDates = STATE.gridData.dates.slice(0, 5);
    const emptyDays = [];

    workDates.forEach((dateStr, idx) => {
        const hasAssignment = row.assignments.some(a => a.date === dateStr);
        if (!hasAssignment) {
            const parts = dateStr.split('-');
            const formattedDate = (parts.length === 3) ? `${parts[2]}/${parts[1]}` : dateStr;
            emptyDays.push({
                dayName: WORK_DAY_NAMES[idx],
                dateStr: dateStr,
                formattedDate: formattedDate
            });
        }
    });

    return emptyDays;
}

function getEmptyWorkDaysSummaryForAll() {
    if (!STATE.gridData || !STATE.gridData.rows) return [];
    const summary = [];

    STATE.gridData.rows.forEach(row => {
        const emptyDays = getEmptyWorkDaysForUser(row);
        if (emptyDays.length > 0) {
            summary.push({
                user: row.user,
                emptyDays: emptyDays
            });
        }
    });

    return summary;
}

function handleSchedulerConfirmClick() {
    const data = STATE.gridData;
    if (!data || !data.rows || !STATE.currentUser) {
        openModal('modal-confirm');
        return;
    }

    const myRow = data.rows.find(r => parseInt(r.user.id) === parseInt(STATE.currentUser.id));
    if (!myRow) {
        openModal('modal-confirm');
        return;
    }

    const emptyDays = getEmptyWorkDaysForUser(myRow);
    if (emptyDays.length > 0) {
        document.getElementById('empty-days-warning-title').textContent = 'Días Laborales sin Turno Asignado';
        document.getElementById('empty-days-warning-desc').textContent =
            'Tienes días laborales (Lunes a Viernes) en tu grilla sin ningún turno asignado:';

        const listEl = document.getElementById('empty-days-list');
        listEl.innerHTML = emptyDays.map(d => `
            <li><span class="font-bold text-amber-950">${d.dayName}</span> (${d.formattedDate})</li>
        `).join('');

        const continueBtn = document.getElementById('btn-empty-days-continue');
        continueBtn.textContent = 'Continuar y Confirmar';
        continueBtn.onclick = () => {
            closeModal('modal-empty-days-warning');
            openModal('modal-confirm');
        };

        openModal('modal-empty-days-warning');
    } else {
        openModal('modal-confirm');
    }
}

function handleAdminConsolidateClick() {
    const summary = getEmptyWorkDaysSummaryForAll();
    if (summary.length > 0) {
        document.getElementById('empty-days-warning-title').textContent = 'Advertencia de Consolidación';
        document.getElementById('empty-days-warning-desc').textContent =
            'Se han detectado colaboradores con días laborales (Lunes a Viernes) sin ningún turno asignado:';

        const listEl = document.getElementById('empty-days-list');
        listEl.innerHTML = summary.map(item => {
            const daysText = item.emptyDays.map(d => `<span class="font-bold text-amber-950">${d.dayName}</span> (${d.formattedDate})`).join(', ');
            return `<li><strong class="text-slate-900">${item.user.name}:</strong> ${daysText}</li>`;
        }).join('');

        const continueBtn = document.getElementById('btn-empty-days-continue');
        continueBtn.textContent = 'Continuar y Consolidar';
        continueBtn.onclick = () => {
            closeModal('modal-empty-days-warning');
            consolidateWeeklySchedule();
        };

        openModal('modal-empty-days-warning');
    } else {
        consolidateWeeklySchedule();
    }
}

async function consolidateWeeklySchedule() {
    try {
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/consolidate?admin_id=${STATE.currentUser.id}`, {
            method: 'POST'
        });
        if (res.ok) {
            showToast('Semana publicada', 'success');
            await loadGrid();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Error al consolidar', 'warning');
        }
    } catch (err) {
        console.error(err);
    }
}

// --- Funciones de Exportación por Rango de Fechas ---
function openExportModal() {
    const today = new Date();
    const y = today.getFullYear();
    const m = today.getMonth();
    const lastDay = new Date(y, m + 1, 0);

    const fStart = `${y}-${String(m + 1).padStart(2, '0')}-01`;
    const fEnd = `${y}-${String(m + 1).padStart(2, '0')}-${String(lastDay.getDate()).padStart(2, '0')}`;

    document.getElementById('export-start-date').value = fStart;
    document.getElementById('export-end-date').value = fEnd;
    openModal('modal-export');
}

function triggerExport(format) {
    const startDate = document.getElementById('export-start-date').value;
    const endDate = document.getElementById('export-end-date').value;

    if (!startDate || !endDate) {
        alert('Por favor selecciona el rango de fechas');
        return;
    }

    closeModal('modal-export');
    window.location.href = `/api/schedules/export?team_id=${STATE.currentTeamId}&start_date=${startDate}&end_date=${endDate}&format=${format}`;
}

async function adminReopenSchedule(userId) {
    if (!confirm('¿Reabrir el horario de este miembro a BORRADOR para permitir ajustes?')) return;
    try {
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/reopen`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_id: userId,
                admin_id: STATE.currentUser.id,
                notes: 'Reapertura acordada por Admin para ajustar discrepancia'
            })
        });
        if (res.ok) {
            showToast('Semana reabierta a borrador para el miembro.', 'info');
            await loadGrid();
        }
    } catch (err) {
        console.error(err);
    }
}

// --- Configuración de Modales y Event Listeners ---
function setupEventListeners() {
    document.getElementById('btn-prev-week').addEventListener('click', () => {
        if (STATE.currentWeek > 1) {
            STATE.currentWeek--;
        } else {
            STATE.currentYear--;
            STATE.currentWeek = getISOWeeksInYear(STATE.currentYear);
        }
        loadGrid();
    });
    document.getElementById('btn-next-week').addEventListener('click', () => {
        const totalWeeks = getISOWeeksInYear(STATE.currentYear);
        if (STATE.currentWeek < totalWeeks) {
            STATE.currentWeek++;
        } else {
            STATE.currentYear++;
            STATE.currentWeek = 1;
        }
        loadGrid();
    });

    const btnToday = document.getElementById('btn-today-week');
    if (btnToday) {
        btnToday.addEventListener('click', () => {
            const current = getISOWeekAndYear();
            STATE.currentWeek = current.week;
            STATE.currentYear = current.year;
            loadGrid();
        });
    }

    // Enviar Confirmación (HU-06)
    document.getElementById('btn-submit-confirm').addEventListener('click', async () => {
        const comment = document.getElementById('confirm-comment').value.trim();
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/confirm`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: STATE.currentUser.id, comment: comment })
        });
        if (res.ok) {
            closeModal('modal-confirm');
            showToast('Horario confirmado exitosamente.', 'success');
            await loadGrid();
        } else {
            const err = await res.json();
            alert(err.detail || 'Error al confirmar');
        }
    });

    // Enviar Rechazo (HU-07)
    document.getElementById('btn-submit-reject').addEventListener('click', async () => {
        const reason = document.getElementById('reject-reason').value.trim();
        const errEl = document.getElementById('reject-error');

        if (!reason || reason.length < 3) {
            errEl.classList.remove('hidden');
            return;
        }
        errEl.classList.add('hidden');

        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/reject`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: STATE.currentUser.id, reason: reason })
        });

        if (res.ok) {
            closeModal('modal-reject');
            document.getElementById('reject-reason').value = '';
            showToast('Discrepancia registrada y horario marcado como RECHAZADO.', 'warning');
            await loadGrid();
        } else {
            const err = await res.json();
            alert(err.detail || 'Error al rechazar');
        }
    });

    // Revertir a Confirmado (HU-07)
    document.getElementById('btn-submit-revert').addEventListener('click', async () => {
        const comment = document.getElementById('revert-comment').value.trim();
        const res = await fetch(`/api/schedules/${STATE.gridData.schedule.id}/revert`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: STATE.currentUser.id, comment: comment })
        });

        if (res.ok) {
            closeModal('modal-revert');
            showToast('Rechazo revertido y horario confirmado.', 'success');
            await loadGrid();
        } else {
            const err = await res.json();
            alert(err.detail || 'Error al revertir');
        }
    });

    // Confirmar eliminación por Admin de bloque confirmado
    const btnAdminDel = document.getElementById('btn-admin-confirm-delete');
    if (btnAdminDel) {
        btnAdminDel.addEventListener('click', async () => {
            closeModal('modal-admin-delete-confirmed');
            if (targetAssignmentIdToDelete) {
                await executeDeleteAssignment(targetAssignmentIdToDelete);
                targetAssignmentIdToDelete = null;
            }
        });
    }
}

function openModal(id) {
    document.getElementById(id).classList.remove('hidden');
}

function closeModal(id) {
    document.getElementById(id).classList.add('hidden');
}

function showToast(msg, type = 'info') {
    const banner = document.getElementById('alert-banner');
    banner.className = `mb-4 p-3.5 rounded-xl border flex items-center justify-between text-xs font-semibold ${
        type === 'success' ? 'bg-emerald-50 text-emerald-800 border-emerald-200' :
        type === 'warning' ? 'bg-rose-50 text-rose-800 border-rose-200' : 'bg-blue-50 text-blue-800 border-blue-200'
    }`;
    banner.innerHTML = `
        <div class="flex items-center gap-2">
            <i class="ph-bold ${type === 'success' ? 'ph-check-circle' : 'ph-info'} text-base"></i>
            <span>${msg}</span>
        </div>
        <button onclick="document.getElementById('alert-banner').classList.add('hidden')" class="hover:opacity-75">
            <i class="ph-bold ph-x"></i>
        </button>
    `;
    banner.classList.remove('hidden');
    setTimeout(() => { banner.classList.add('hidden'); }, 6000);
}
