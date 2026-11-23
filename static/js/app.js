const teams = [
  { id: "A", name: "Muži A", league: "Joola Extraliga", coef: 10 },
  { id: "B", name: "Muži B", league: "2. liga KSTZ TN", coef: 3.5 },
  { id: "C", name: "Muži C", league: "3. liga KSTZ TN", coef: 2.25 },
  { id: "D", name: "Muži D", league: "4. liga KSTZ TN", coef: 2 },
  { id: "E", name: "Muži E", league: "5. liga OSTZ TN", coef: 1.00 },
  { id: "F", name: "Muži F", league: "5. liga OSTZ TN", coef: 1.00 },
  { id: "G", name: "Muži G", league: "6. liga OSTZ TN", coef: 0.85 },
  { id: "H", name: "Ženy", league: "Extraliga", coef: 5 }
];

const TAB_ROUTES = {
  competitions: "/zapasy",
  signup: "/prihlasovanie",
  leaderboard: "/rebricek",
  profile: "/profil",
  admin: "/sprava-uctov"
};

const ROUTE_TABS = {
  "/": "competitions",
  "/zapasy": "competitions",
  "/prihlasovanie": "signup",
  "/rebricek": "leaderboard",
  "/profil": "profile",
  "/sprava-uctov": "admin"
};

let matches = [];
let clubPlayers = [];
let opponentsCache = {};
let opponentLoadErrors = {};
let currentUser = null;
let csrfToken = "";
let activeTeamId = "A";
let activeMainTab = "competitions";
let expandedMatchId = null;

// Stav pre profil hráča a preklikávanie súťaží v profile
let viewedProfilePlayerId = null;
let activeProfileLeague = "ALL";

async function loadData() {
  currentUser = null;
  csrfToken = "";
  try {
    const sessionResponse = await fetch("/api/auth/me");
    if (sessionResponse.ok) {
      currentUser = await sessionResponse.json();
    } else if (sessionResponse.status === 401) {
      currentUser = null;
    } else {
      throw new Error(await responseError(sessionResponse));
    }
    csrfToken = currentUser ? currentUser.csrfToken : "";

    const resMatches = await fetch("/api/matches");
    if (!resMatches.ok) throw new Error("Nepodarilo sa načítať zápasy.");
    matches = await resMatches.json();

    const resPlayers = await fetch("/api/players");
    if (!resPlayers.ok) throw new Error("Nepodarilo sa načítať hráčov.");
    clubPlayers = await resPlayers.json();

    if (matches.length > 0) {
      const first = matches.find(m => m.teamId === activeTeamId);
      if (first) {
        expandedMatchId = first.id;
        await loadOpponents(first.id);
      }
    }
  } catch (e) {
    console.error("Chyba pri načítaní dát:", e);
  }

  syncTabFromUrl(false);
  renderAll();
  if (currentUser && currentUser.mustChangePassword) openPasswordDialog(true);
}

function getCurrentPlayer() {
  if (!currentUser || currentUser.role !== "player" || currentUser.mustChangePassword) return null;
  return clubPlayers.find(p => p.id === currentUser.playerId);
}

function canViewSignup() {
  return Boolean(getCurrentPlayer()) ||
    (currentUser?.role === "admin" && !currentUser.mustChangePassword);
}

function getTeamName(teamId) {
  const team = teams.find(t => t.id === teamId);
  return team ? team.name : teamId;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  })[char]);
}

function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  return fetch(url, { ...options, headers, credentials: "same-origin" });
}

async function responseError(response) {
  const result = await response.json().catch(() => ({}));
  return result.detail || `Požiadavka zlyhala (${response.status}).`;
}

function openLogin() {
  const dialog = document.getElementById("login-dialog");
  if (!dialog.open) dialog.showModal();
  document.getElementById("login-username").focus();
}

function closeLogin() {
  const form = document.getElementById("login-form");
  form.reset();
  const passwordInput = document.getElementById("login-password");
  const toggleButton = document.getElementById("login-password-toggle");
  passwordInput.type = "password";
  toggleButton.innerText = "Zobraziť";
  toggleButton.setAttribute("aria-label", "Zobraziť heslo");
  toggleButton.setAttribute("aria-pressed", "false");
  document.getElementById("login-dialog").close();
}

function toggleLoginPasswordVisibility() {
  const passwordInput = document.getElementById("login-password");
  const toggleButton = document.getElementById("login-password-toggle");
  const visible = passwordInput.type === "password";
  passwordInput.type = visible ? "text" : "password";
  toggleButton.innerText = visible ? "Skryť" : "Zobraziť";
  toggleButton.setAttribute("aria-label", visible ? "Skryť heslo" : "Zobraziť heslo");
  toggleButton.setAttribute("aria-pressed", String(visible));
}

function openPasswordDialog(forced) {
  const dialog = document.getElementById("password-dialog");
  dialog.dataset.forced = String(forced);
  document.getElementById("password-dialog-title").innerText = forced
    ? "Nastavte si nové heslo"
    : "Zmeniť heslo";
  const currentField = document.getElementById("current-password-field");
  currentField.classList.toggle("hidden", forced);
  currentField.querySelector("input").required = !forced;
  document.getElementById("password-cancel-btn").classList.toggle("hidden", forced);
  document.getElementById("password-error").classList.add("hidden");
  if (!dialog.open) dialog.showModal();
}

function closePasswordDialog() {
  if (currentUser?.mustChangePassword) return;
  document.getElementById("password-dialog").close();
}

async function submitLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = document.getElementById("login-error");
  error.classList.add("hidden");
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        username: form.elements.username.value,
        password: form.elements.password.value
      })
    });
    if (!response.ok) throw new Error(await responseError(response));
    currentUser = await response.json();
    csrfToken = currentUser.csrfToken;
    form.reset();
    closeLogin();
    await loadData();
  } catch (e) {
    error.innerText = e.message;
    error.classList.remove("hidden");
  }
}

async function submitPasswordChange(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = document.getElementById("password-error");
  error.classList.add("hidden");
  if (form.elements.newPassword.value !== form.elements.confirmPassword.value) {
    error.innerText = "Nové heslá sa nezhodujú.";
    error.classList.remove("hidden");
    return;
  }

  try {
    const response = await apiFetch("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        currentPassword: form.elements.currentPassword.value,
        newPassword: form.elements.newPassword.value
      })
    });
    if (!response.ok) throw new Error(await responseError(response));
    currentUser = await response.json();
    csrfToken = currentUser.csrfToken;
    form.reset();
    document.getElementById("password-dialog").close();
    await loadData();
  } catch (e) {
    error.innerText = e.message;
    error.classList.remove("hidden");
  }
}

async function logout() {
  try {
    const response = await apiFetch("/api/auth/logout", { method: "POST" });
    if (!response.ok) throw new Error(await responseError(response));
    currentUser = null;
    csrfToken = "";
    activeMainTab = "competitions";
    document.getElementById("admin-credential-output").replaceChildren();
    document.getElementById("admin-credential-output").classList.add("hidden");
    await loadData();
  } catch (e) {
    window.alert(e.message);
  }
}

function updateNavigation() {
  const isPlayer = getCurrentPlayer() !== null;
  const isAdmin = currentUser?.role === "admin" && !currentUser.mustChangePassword;
  document.getElementById("nav-signup").classList.toggle("hidden", !canViewSignup());
  document.getElementById("nav-profile").classList.toggle("hidden", !isPlayer);
  document.getElementById("nav-admin").classList.toggle("hidden", !isAdmin);
  document.getElementById("login-open-btn").classList.toggle("hidden", Boolean(currentUser));
  document.getElementById("password-change-btn").classList.toggle("hidden", !currentUser);
  document.getElementById("logout-btn").classList.toggle("hidden", !currentUser);
  document.getElementById("current-user-label").innerText = currentUser
    ? (currentUser.name || currentUser.username)
    : "";

  ["competitions", "signup", "leaderboard", "profile", "admin"].forEach(t => {
    document.getElementById(`tab-${t}`).classList.toggle("hidden", t !== activeMainTab);
    const btn = document.getElementById(`nav-${t}`);
    if (btn) btn.classList.toggle("active", t === activeMainTab);
  });
}

function switchMainTab(tab, updateUrl = true) {
  if (tab === "signup" && !canViewSignup()) {
    openLogin();
    return;
  }
  if (tab === "admin" && (currentUser?.role !== "admin" || currentUser.mustChangePassword)) return;
  activeMainTab = tab;
  if (tab === "profile" && !viewedProfilePlayerId) {
    const me = getCurrentPlayer();
    viewedProfilePlayerId = me ? me.id : (clubPlayers[0] ? clubPlayers[0].id : null);
  }
  updateNavigation();
  renderAll();
  if (tab === "admin") loadAdminAccounts();

  if (updateUrl && TAB_ROUTES[tab]) {
    window.history.pushState({ tab: tab }, "", TAB_ROUTES[tab]);
  }
}

window.addEventListener("popstate", (event) => {
  if (event.state && event.state.tab) {
    switchMainTab(event.state.tab, false);
  } else {
    syncTabFromUrl(false);
  }
});

function syncTabFromUrl(updateUrl = false) {
  const path = window.location.pathname;
  let tab = ROUTE_TABS[path] || "competitions";
  if (tab === "signup" && !canViewSignup()) tab = "competitions";
  if (tab === "admin" && currentUser?.role !== "admin") tab = "competitions";
  switchMainTab(tab, updateUrl);
}

async function selectTeam(teamId) {
  activeTeamId = teamId;
  const first = matches.find(m => m.teamId === teamId);
  expandedMatchId = first ? first.id : null;
  renderAll();
  if (expandedMatchId) {
    await loadOpponents(expandedMatchId);
    renderCompetitions();
  }
}

async function toggleMatch(matchId) {
  if (expandedMatchId === matchId) {
    expandedMatchId = null;
  } else {
    expandedMatchId = matchId;
    await loadOpponents(matchId);
  }
  renderCompetitions();
}

async function loadOpponents(matchId) {
  if (Object.hasOwn(opponentsCache, matchId)) return;
  try {
    const res = await fetch(`/api/matches/${matchId}/opponents`);
    if (!res.ok) throw new Error(await responseError(res));
    opponentsCache[matchId] = await res.json();
    delete opponentLoadErrors[matchId];
  } catch (e) {
    opponentLoadErrors[matchId] = e.message;
    console.error(`Nepodarilo sa načítať úspešnosť súpera pre zápas ${matchId}:`, e);
  }
}

function getPlayerStatForLeague(player, teamId) {
  const st = (player.leagueStats || []).find(s => s.leagueId === teamId);
  if (st) return st;
  return {
    leagueId: teamId,
    encounters: 0,
    teamPlayed: 1,
    attendancePct: 0,
    qualifies: false,
    won: 0,
    lost: 0,
    totalMatches: 0,
    winRate: 0
  };
}

function renderCompetitions() {
  const me = getCurrentPlayer();
  document.getElementById("team-selector").innerHTML = teams.map(t => {
    const onRoster = me && me.roster.includes(t.id);
    return `
      <button class="team-tab ${t.id === activeTeamId ? 'active' : ''}" onclick="selectTeam('${t.id}')">
        <span>${t.name}</span>
        ${onRoster ? '<span class="roster-dot" title="Si na súpiske"></span>' : ''}
      </button>
    `;
  }).join("");

  const team = teams.find(t => t.id === activeTeamId);
  document.getElementById("comp-title").innerText = `${team.name} – ${team.league}`;
  document.getElementById("comp-subtitle").innerText = `Koeficient súťaže pre klubový rebríček: ×${team.coef.toFixed(2)} (podmienka: aspoň 40 % odohraných stretnutí družstva)`;

  const teamMatches = matches.filter(m => m.teamId === activeTeamId);
  const rosterPlayers = clubPlayers
    .filter(p => p.roster.includes(activeTeamId) || (p.leagueStats || []).some(s => s.leagueId === activeTeamId))
    .map(p => ({ ...p, lgStat: getPlayerStatForLeague(p, activeTeamId) }))
    .sort((a, b) => b.lgStat.winRate - a.lgStat.winRate || b.lgStat.won - a.lgStat.won);

  if (teamMatches.length === 0) {
    document.getElementById("matches-container").innerHTML = `<div class="slot-card">Zatiaľ žiadne stiahnuté zápasy pre toto družstvo.</div>`;
    return;
  }

  document.getElementById("matches-container").innerHTML = teamMatches.map(m => {
    const isExp = expandedMatchId === m.id;
    const opps = opponentsCache[m.id] || [];
    const oppTeam = m.isHome ? m.away : m.home;
    const matchDate = m.date || "Termín neurčený";

    return `
      <div class="match-card ${isExp ? 'expanded' : ''}">
        <div class="match-row" onclick="toggleMatch(${m.id})">
          <div style="display:flex;align-items:center;gap:10px;">
            <span class="${m.isHome ? 'tag-home' : 'tag-away'}">${m.isHome ? 'DOMA' : 'VONKU'}</span>
            <span style="font-size:12px;color:#71717a;"><strong>${escapeHtml(m.round)}</strong> • ${escapeHtml(matchDate)} • ${escapeHtml(m.slot)}</span>
          </div>
          <div class="match-teams">${escapeHtml(m.home)} <span style="color:#71717a;font-weight:400;">vs</span> ${escapeHtml(m.away)}</div>
          <div>
            ${m.status === 'played' ? `<span class="score-pill">${escapeHtml(m.score)}</span>` : `<span class="slot-pill">${escapeHtml(m.slot)}</span>`}
          </div>
        </div>
        ${isExp ? `
          <div class="match-detail">
            <div class="stats-grid">
              <div class="stats-box">
                <div class="stats-box-header">
                  <span>Úspešnosť v ${team.league}: Keraming (${escapeHtml(m.teamId)})</span>
                  <span>Výhry:Prehry (Záp.) • Úsp.</span>
                </div>
                ${rosterPlayers.length > 0 ? rosterPlayers.map(p => {
                  const st = p.lgStat;
                  const total = st.won + st.lost;
                  const pct = total > 0 ? Math.round((st.won / total) * 100) : 0;
                  return `
                    <div class="player-stat-row">
                      <div>
                        <span class="player-link" onclick="openPlayerProfile(${p.id}, '${activeTeamId}')">${escapeHtml(p.name)}</span>
                        <span style="font-size:11px;color:#71717a;margin-left:6px;">(${st.encounters}/${st.teamPlayed} stret. • ${st.attendancePct} %)</span>
                      </div>
                      <span><strong>${st.won}:${st.lost}</strong> <span style="color:#71717a;font-size:11px;">(${total} záp.)</span> • <strong>${pct} %</strong></span>
                    </div>
                  `;
                }).join("") : '<div style="font-size:12px;color:#71717a;">Žiadni hráči na súpiske.</div>'}
              </div>

              <div class="stats-box">
                <div class="stats-box-header">
                  <span>Úspešnosť súpera: ${escapeHtml(oppTeam)}</span>
                  <span>Výhry:Prehry (Záp.) • Úsp.</span>
                </div>
                ${opponentLoadErrors[m.id]
                  ? `<div style="font-size:12px;color:#dc2626;padding:8px 0;">${escapeHtml(opponentLoadErrors[m.id])}</div>`
                  : opps.length > 0 ? opps.map(o => {
                  const total = o.won + o.lost;
                  const pct = total > 0 ? Math.round((o.won / total) * 100) : 0;
                  return `
                    <div class="player-stat-row">
                      <span><strong>${escapeHtml(o.name)}</strong></span>
                      <span><strong>${o.won}:${o.lost}</strong> <span style="color:#71717a;font-size:11px;">(${total} záp.)</span> • <strong>${pct} %</strong></span>
                    </div>
                  `;
                }).join("") : '<div style="font-size:12px;color:#71717a;padding:8px 0;">Pre tohto súpera zatiaľ nie sú evidované odohrané dvojhry.</div>'}
              </div>
            </div>
          </div>
        ` : ''}
      </div>
    `;
  }).join("");
}

function renderSignupTab() {
  const me = getCurrentPlayer();
  const isAdminViewer = currentUser?.role === "admin" && !currentUser.mustChangePassword;
  if (!me && !isAdminViewer) {
    document.getElementById("my-signup-box").innerHTML = "";
    document.getElementById("signup-table-body").innerHTML = "";
    return;
  }

  if (me) {
    document.getElementById("my-signup-box").innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div>
        <span style="font-size:15px;font-weight:800;">Moja dochádzka na sobotu: ${escapeHtml(me.name)}</span>
        <span style="font-size:12px;color:#71717a;margin-left:8px;">(Tvoja súpiska: ${me.roster.map(getTeamName).join(', ')})</span>
      </div>
      <span id="save-indicator" style="font-size:12px;font-weight:700;color:#16a34a;"></span>
    </div>

    <div class="signup-group-title">1. Kedy môžeš v sobotu hrať?</div>
    <div class="check-pills">
      <div class="check-pill ${me.canMorning && !me.cannotPlay ? 'checked' : ''}" onclick="toggleTimeSlot('morning')">
        <span>${me.canMorning && !me.cannotPlay ? '☑️' : '⬜'}</span>
        <span>☀️ Sobota DOOBEDA (10:00)</span>
      </div>
      <div class="check-pill ${me.canAfternoon && !me.cannotPlay ? 'checked' : ''}" onclick="toggleTimeSlot('afternoon')">
        <span>${me.canAfternoon && !me.cannotPlay ? '☑️' : '⬜'}</span>
        <span>🌤️ Sobota POOBEDE (14:00+)</span>
      </div>
      <div class="check-pill ${me.cannotPlay ? 'checked-red' : ''}" onclick="toggleTimeSlot('cannot')">
        <span>${me.cannotPlay ? '🚫' : '⬜'}</span>
        <span>Nemôžem túto sobotu</span>
      </div>
    </div>

    <div class="signup-group-title">2. Ktorú ligu (družstvo) chceš hrať?</div>
    <div class="check-pills">
      ${teams.map(t => {
        const onRoster = me.roster.includes(t.id);
        const isChecked = me.selectedLeagues.includes(t.id) && !me.cannotPlay;
        if (!onRoster) {
          return `
            <div class="check-pill disabled" title="Nie si na súpiske tohto družstva">
              <span>🔒 ${t.name} (${t.league})</span>
            </div>
          `;
        }
        return `
          <div class="check-pill ${isChecked ? 'checked' : ''}" onclick="toggleLeagueChoice('${t.id}')">
            <span>${isChecked ? '☑️' : '⬜'}</span>
            <span>${t.name} (${t.league})</span>
          </div>
        `;
      }).join("")}
    </div>

    <div class="save-bar">
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;cursor:pointer;">
        <input type="checkbox" ${me.takesCar ? 'checked' : ''} onchange="toggleMyCar(this.checked)">
        <span>🚗 Môžem zobrať auto</span>
      </label>
      <input type="text" class="note-input" value="${escapeHtml(me.note || '')}" onchange="updateMyNote(this.value)" placeholder="Poznámka (napr. môžem len 1 zápas / beriem 3 ľudí)...">
      <button class="btn-primary" onclick="saveMySignup()">Uložiť moju voľbu</button>
    </div>
    `;
  } else {
    document.getElementById("my-signup-box").innerHTML = `
      <div style="font-size:13px;color:#71717a;">
        Prehľad prihlásení hráčov (režim iba na čítanie).
      </div>
    `;
  }

  document.getElementById("signup-table-body").innerHTML = clubPlayers.map(p => {
    const isMe = me && p.id === me.id;
    return `
      <tr class="${isMe ? 'me-row' : ''}">
        <td>
          <strong>${escapeHtml(p.name)}</strong> ${isMe ? '<span style="font-size:11px;font-weight:700;color:#ca8a04;">(Ty)</span>' : ''}
          <div style="font-size:11px;color:#71717a;">Súpiska: ${p.roster.map(getTeamName).join(", ")}</div>
        </td>
        <td>
          ${p.cannotPlay ? '<span class="cannot-badge">✕ Nemôže</span>' : (p.canMorning ? '<span class="yes-badge">✓ Môže doobeda</span>' : '<span class="no-badge">—</span>')}
        </td>
        <td>
          ${p.cannotPlay ? '<span class="cannot-badge">✕ Nemôže</span>' : (p.canAfternoon ? '<span class="yes-badge">✓ Môže poobede</span>' : '<span class="no-badge">—</span>')}
        </td>
        <td>
          ${p.selectedLeagues.length > 0 && !p.cannotPlay
            ? p.selectedLeagues.map(lg => {
                const t = teams.find(x => x.id === lg);
                return `<span class="league-badge">${t ? t.name + ' (' + t.league + ')' : lg}</span>`;
              }).join("")
            : '<span style="color:#a1a1aa;font-size:12px;">Žiadna zvolená</span>'
          }
        </td>
        <td>
          ${p.takesCar && !p.cannotPlay ? '<span style="font-size:12px;font-weight:700;color:#1d4ed8;">🚗 Auto</span> ' : ''}
          <span style="font-size:12px;color:#71717a;">${escapeHtml(p.note || '')}</span>
        </td>
      </tr>
    `;
  }).join("");
}

function toggleTimeSlot(slot) {
  const me = getCurrentPlayer();
  if (!me) return;
  if (slot === "cannot") {
    me.cannotPlay = !me.cannotPlay;
    if (me.cannotPlay) {
      me.canMorning = false;
      me.canAfternoon = false;
      me.selectedLeagues = [];
    }
  } else if (slot === "morning") {
    me.cannotPlay = false;
    me.canMorning = !me.canMorning;
  } else if (slot === "afternoon") {
    me.cannotPlay = false;
    me.canAfternoon = !me.canAfternoon;
  }
  saveMySignup();
}

function toggleLeagueChoice(teamId) {
  const me = getCurrentPlayer();
  if (!me || !me.roster.includes(teamId)) return;
  me.cannotPlay = false;
  if (me.selectedLeagues.includes(teamId)) {
    me.selectedLeagues = me.selectedLeagues.filter(x => x !== teamId);
  } else {
    me.selectedLeagues.push(teamId);
  }
  saveMySignup();
}

function toggleMyCar(checked) {
  const me = getCurrentPlayer();
  if (!me) return;
  me.takesCar = checked;
  saveMySignup();
}

function updateMyNote(val) {
  const me = getCurrentPlayer();
  if (!me) return;
  me.note = val;
  saveMySignup();
}

async function saveMySignup() {
  const me = getCurrentPlayer();
  if (!me) return;
  renderSignupTab();
  try {
    const response = await apiFetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        canMorning: me.canMorning,
        canAfternoon: me.canAfternoon,
        cannotPlay: me.cannotPlay,
        selectedLeagues: me.selectedLeagues,
        takesCar: me.takesCar,
        note: me.note
      })
    });
    if (!response.ok) throw new Error(await responseError(response));
    const ind = document.getElementById("save-indicator");
    if (ind) ind.innerText = "✓ Uložené v databáze";
  } catch (e) {
    const ind = document.getElementById("save-indicator");
    if (ind) {
      ind.innerText = e.message;
      ind.style.color = "#dc2626";
    }
  }
}

function suggestedUsername(name) {
  const username = name.normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, ".")
    .replace(/^[._-]+|[._-]+$/g, "")
    .slice(0, 32);
  return username.length >= 3 ? username : `hrac${Date.now().toString().slice(-6)}`;
}

function showAdminMessage(message, isError = false) {
  const box = document.getElementById("admin-message");
  box.innerText = message;
  box.style.color = isError ? "#dc2626" : "#16a34a";
  box.classList.remove("hidden");
}

function showTemporaryCredentials(username, temporaryPassword, action) {
  const box = document.getElementById("admin-credential-output");
  box.replaceChildren();
  const title = document.createElement("strong");
  title.innerText = action;
  const details = document.createElement("p");
  details.innerText = `Meno: ${username} · Dočasné heslo: `;
  const password = document.createElement("code");
  password.innerText = temporaryPassword;
  details.append(password);
  const note = document.createElement("p");
  note.innerText = "Zobrazí sa iba teraz. Odovzdaj ho hráčovi súkromne; po prihlásení si musí nastaviť nové heslo.";
  box.append(title, details, note);
  box.classList.remove("hidden");
}

async function loadAdminAccounts() {
  const tbody = document.getElementById("admin-accounts-body");
  tbody.innerHTML = `<tr><td colspan="3">Načítavam účty…</td></tr>`;
  document.getElementById("admin-message").classList.add("hidden");
  try {
    const response = await apiFetch("/api/admin/accounts");
    if (!response.ok) throw new Error(await responseError(response));
    const accounts = await response.json();
    tbody.innerHTML = accounts.map(account => `
      <tr>
        <td>
          <strong>${escapeHtml(account.name)}</strong>
          <div style="font-size:11px;color:#71717a;">Súpiska: ${account.roster.map(getTeamName).join(", ")}</div>
        </td>
        <td>
          <input id="admin-username-${account.playerId}" class="account-username-input" value="${escapeHtml(account.username || suggestedUsername(account.name))}" maxlength="32" aria-label="Prihlasovacie meno pre ${escapeHtml(account.name)}">
        </td>
        <td>
          <div class="account-actions">
            ${account.username
              ? `<button class="btn-primary" type="button" onclick="updatePlayerUsername(${account.playerId})">Uložiť meno</button>
                 <button class="nav-btn" type="button" onclick="resetPlayerPassword(${account.playerId})">Resetovať heslo</button>`
              : `<button class="btn-primary" type="button" onclick="createPlayerAccount(${account.playerId})">Vytvoriť účet</button>`}
          </div>
        </td>
      </tr>
    `).join("");
  } catch (e) {
    tbody.innerHTML = "";
    showAdminMessage(e.message, true);
  }
}

async function createPlayerAccount(playerId) {
  const username = document.getElementById(`admin-username-${playerId}`).value;
  await runAdminAccountAction(`/api/admin/accounts`, "POST", { playerId, username }, "Účet bol vytvorený.");
}

async function updatePlayerUsername(playerId) {
  const username = document.getElementById(`admin-username-${playerId}`).value;
  await runAdminAccountAction(`/api/admin/accounts/${playerId}`, "PUT", { username }, "Prihlasovacie meno bolo zmenené.");
}

async function resetPlayerPassword(playerId) {
  if (!window.confirm("Nastaviť hráčovi nové dočasné heslo? Jeho existujúce prihlásenia sa ukončia.")) return;
  await runAdminAccountAction(`/api/admin/accounts/${playerId}/reset-password`, "POST", {}, "Heslo bolo resetované.");
}

async function resetSignupsNow() {
  if (!window.confirm("Naozaj resetovať prihlásenie na zápasy všetkým hráčom?")) return;
  await runAdminAccountAction("/api/admin/signup/reset", "POST", {}, "Prihlásenia na zápasy boli resetované.");
}

async function runAdminAccountAction(url, method, body, successMessage) {
  const credentials = document.getElementById("admin-credential-output");
  credentials.replaceChildren();
  credentials.classList.add("hidden");
  try {
    const response = await apiFetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(await responseError(response));
    const result = await response.json();
    await loadAdminAccounts();
    if (result.temporaryPassword) {
      showTemporaryCredentials(result.username, result.temporaryPassword, successMessage);
    } else {
      showAdminMessage(successMessage);
    }
  } catch (e) {
    showAdminMessage(e.message, true);
  }
}

// Vyberie súťaž hráča, kde má odohraných >= 40 % stretnutí a najvyšší K-Index
function getQualifiedStatForLeaderboard(player) {
  const stats = player.leagueStats || [];
  const qualified = stats
    .filter(s => s.qualifies)
    .map(s => {
      const team = teams.find(t => t.id === s.leagueId) || teams[0];
      const total = s.won + s.lost;
      const pct = total > 0 ? Math.round((s.won / total) * 100) : 0;
      const kIndex = +(pct * team.coef).toFixed(1);
      return { ...s, team, pct, kIndex };
    })
    .sort((a, b) => b.kIndex - a.kIndex);

  if (qualified.length > 0) {
    return qualified[0];
  }

  // Ak nespĺňa 40 % v žiadnej súťaži, vyberieme súťaž s najvyšším počtom zápasov
  const sortedByGames = [...stats].sort((a, b) => (b.won + b.lost) - (a.won + a.lost));
  const fallbackStat = sortedByGames[0] || {
    leagueId: player.teamId,
    encounters: 0,
    teamPlayed: 1,
    attendancePct: 0,
    won: 0,
    lost: 0
  };
  const team = teams.find(t => t.id === fallbackStat.leagueId) || teams[0];
  const total = fallbackStat.won + fallbackStat.lost;
  const pct = total > 0 ? Math.round((fallbackStat.won / total) * 100) : 0;
  return {
    ...fallbackStat,
    qualifies: false,
    team,
    pct,
    kIndex: 0
  };
}

function renderLeaderboard() {
  const evaluated = clubPlayers.map(p => {
    const best = getQualifiedStatForLeaderboard(p);
    return { ...p, evalStat: best };
  });

  const qualifiedList = evaluated
    .filter(p => p.evalStat.qualifies)
    .sort((a, b) => b.evalStat.kIndex - a.evalStat.kIndex || b.evalStat.pct - a.evalStat.pct);

  const unqualifiedList = evaluated
    .filter(p => !p.evalStat.qualifies)
    .sort((a, b) => b.evalStat.pct - a.evalStat.pct || (b.evalStat.won + b.evalStat.lost) - (a.evalStat.won + a.evalStat.lost));

  let html = qualifiedList.map((p, i) => {
    const s = p.evalStat;
    const total = s.won + s.lost;
    return `
      <tr style="cursor:pointer;" onclick="openPlayerProfile(${p.id}, '${s.team.id}')">
        <td><strong>${i + 1}.</strong></td>
        <td><span class="player-link">${escapeHtml(p.name)}</span></td>
        <td>
          <strong>${s.team.name}</strong> – ${s.team.league} (×${s.team.coef.toFixed(2)})
          <div style="font-size:11px;color:#16a34a;font-weight:600;">✓ Účasť: ${s.encounters}/${s.teamPlayed} stret. (${s.attendancePct} %)</div>
        </td>
        <td><strong>${s.won} : ${s.lost}</strong> <span style="font-size:11px;color:#71717a;">(${total} záp.)</span></td>
        <td><strong>${s.pct} %</strong></td>
        <td><strong style="background:#fef9c3;padding:3px 8px;border-radius:6px;border:1px solid #facc15;">${s.kIndex} b</strong></td>
      </tr>
    `;
  }).join("");

  if (unqualifiedList.length > 0) {
    html += `
      <tr>
        <td colspan="6" style="background:#f4f4f5;font-size:11px;font-weight:800;text-transform:uppercase;color:#71717a;padding:8px 14px;">
          Mimo poradia – menej ako 40 % odohraných stretnutí v súťaži
        </td>
      </tr>
    `;
    html += unqualifiedList.map(p => {
      const s = p.evalStat;
      const total = s.won + s.lost;
      return `
        <tr style="cursor:pointer;opacity:0.65;" onclick="openPlayerProfile(${p.id}, '${s.team.id}')">
          <td>—</td>
          <td><span class="player-link">${escapeHtml(p.name)}</span></td>
          <td>
            ${s.team.name} – ${s.team.league}
            <div style="font-size:11px;color:#dc2626;">Účasť: ${s.encounters}/${s.teamPlayed} stret. (${s.attendancePct} % &lt; 40 %)</div>
          </td>
          <td>${s.won} : ${s.lost} <span style="font-size:11px;color:#71717a;">(${total} záp.)</span></td>
          <td>${s.pct} %</td>
          <td><span class="no-badge">Neklasifikovaný</span></td>
        </tr>
      `;
    }).join("");
  }

  document.getElementById("leaderboard-body").innerHTML = html;
}

function openMyProfile() {
  const me = getCurrentPlayer();
  if (me) {
    openPlayerProfile(me.id, "ALL");
  }
}

function openPlayerProfile(playerId, initialLeague = "ALL") {
  viewedProfilePlayerId = playerId;
  activeProfileLeague = initialLeague || "ALL";
  switchMainTab("profile", true);
}

function selectProfileLeague(leagueId) {
  activeProfileLeague = leagueId;
  renderProfile();
}

function renderProfile() {
  const container = document.getElementById("profile-container");
  if (!container) return;

  const targetId = viewedProfilePlayerId || (getCurrentPlayer() ? getCurrentPlayer().id : null);
  const p = clubPlayers.find(x => x.id === targetId);
  if (!p) {
    container.innerHTML = `<div class="my-signup-card">Vyber si hráča v hornej lište pre zobrazenie profilu.</div>`;
    return;
  }

  const stats = p.leagueStats || [];
  const bestQualified = getQualifiedStatForLeaderboard(p);

  // Tlačidlá (taby) na preklikávanie medzi súťažami v profile
  const leagueTabsHtml = `
    <div class="team-tabs" style="margin-top:16px;margin-bottom:16px;">
      <button class="team-tab ${activeProfileLeague === 'ALL' ? 'active' : ''}" onclick="selectProfileLeague('ALL')">
        📊 Všetky súťaže (${stats.length})
      </button>
      ${stats.map(s => {
        const t = teams.find(x => x.id === s.leagueId) || { id: s.leagueId, name: "Muži " + s.leagueId, league: "" };
        const tot = s.won + s.lost;
        const pct = tot > 0 ? Math.round((s.won / tot) * 100) : 0;
        return `
          <button class="team-tab ${activeProfileLeague === s.leagueId ? 'active' : ''}" onclick="selectProfileLeague('${s.leagueId}')">
            <span>🏓 ${t.name} (${t.league})</span>
            <span style="font-size:11px;opacity:0.85;">• ${pct} %</span>
          </button>
        `;
      }).join("")}
    </div>
  `;

  // Detail vybranej súťaže (ak používateľ klikol na konkrétnu súťaž)
  let selectedLeagueDetailHtml = "";
  if (activeProfileLeague !== "ALL") {
    const st = getPlayerStatForLeague(p, activeProfileLeague);
    const t = teams.find(x => x.id === activeProfileLeague) || teams[0];
    const tot = st.won + st.lost;
    const pct = tot > 0 ? Math.round((st.won / tot) * 100) : 0;
    const kIdx = +(pct * t.coef).toFixed(1);

    selectedLeagueDetailHtml = `
      <div class="profile-kpi-grid">
        <div class="profile-kpi-card">
          <div class="signup-group-title">Úspešnosť v ${t.name}</div>
          <div style="font-size:28px;font-weight:900;color:#09090b;">${pct} %</div>
          <div style="font-size:12px;color:#71717a;">${t.league} (Koef. ×${t.coef.toFixed(2)})</div>
        </div>

        <div class="profile-kpi-card">
          <div class="signup-group-title">Dvojhry (Výhry : Prehry)</div>
          <div style="font-size:28px;font-weight:900;color:#09090b;">${st.won} : ${st.lost}</div>
          <div style="font-size:12px;color:#71717a;">Odohraných spolu: <strong>${tot}</strong> dvojhier</div>
        </div>

        <div class="profile-kpi-card">
          <div class="signup-group-title">Účasť na stretnutiach (limit ≥ 40 %)</div>
          <div style="font-size:28px;font-weight:900;color:${st.qualifies ? '#16a34a' : '#dc2626'};">${st.attendancePct} %</div>
          <div style="font-size:12px;color:#71717a;">Nastúpil na <strong>${st.encounters} z ${st.teamPlayed}</strong> stretnutí družstva</div>
        </div>

        <div class="profile-kpi-card">
          <div class="signup-group-title">K-Index zo súťaže</div>
          <div style="font-size:28px;font-weight:900;color:#ca8a04;">${st.qualifies ? kIdx + ' b' : '—'}</div>
          <div style="font-size:12px;color:#71717a;">
            ${st.qualifies ? '✓ Započítava sa do rebríčka klubu' : '✕ Pod hranicou 40 % stretnutí'}
          </div>
        </div>
      </div>
    `;
  }

  // Prehľadová tabuľka všetkých súťaží hráča
  const perLeagueRows = stats.map(s => {
    const t = teams.find(x => x.id === s.leagueId) || teams[0];
    const tot = s.won + s.lost;
    const pct = tot > 0 ? Math.round((s.won / tot) * 100) : 0;
    const kIdx = +(pct * t.coef).toFixed(1);
    const isSelected = activeProfileLeague === s.leagueId;

    return `
      <tr style="cursor:pointer;${isSelected ? 'background:#fefce8;' : ''}" onclick="selectProfileLeague('${s.leagueId}')">
        <td><strong>${t.name} (${t.league})</strong></td>
        <td>×${t.coef.toFixed(2)}</td>
        <td>${s.encounters} z ${s.teamPlayed} (<strong>${s.attendancePct} %</strong>)</td>
        <td><strong>${tot}</strong> záp.</td>
        <td><strong>${s.won} : ${s.lost}</strong></td>
        <td><strong>${pct} %</strong></td>
        <td>
          ${s.qualifies
            ? `<span class="yes-badge">✓ Ráta sa (${kIdx} b)</span>`
            : `<span class="no-badge">Pod 40 % (${s.attendancePct} %)</span>`}
        </td>
      </tr>
    `;
  }).join("");

  container.innerHTML = `
    <div class="my-signup-card">
      <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:12px;">
        <div>
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#71717a;">Karta hráča</div>
          <h2 style="font-size:24px;font-weight:800;margin-top:2px;">${escapeHtml(p.name)}</h2>
          <p style="font-size:13px;color:#71717a;margin-top:2px;">
            Súpiska v klube: <strong>${p.roster.map(getTeamName).join(', ')}</strong>
          </p>
          ${p.stolnytenisUrl
            ? `<a class="external-profile-link" href="${p.stolnytenisUrl}" target="_blank" rel="noopener noreferrer">Profil hráča na stolnytenis.info ↗</a>`
            : ''}
        </div>
        <div style="background:#fef9c3;border:1px solid #facc15;padding:10px 14px;border-radius:10px;text-align:right;">
          <div style="font-size:11px;font-weight:800;text-transform:uppercase;color:#71717a;">Započítané do rebríčka (≥ 40 %)</div>
          <div style="font-size:18px;font-weight:900;color:#09090b;">
            ${bestQualified.qualifies
              ? `${bestQualified.team.name}: ${bestQualified.pct} \% (${bestQualified.kIndex} b)`
              : 'Zatiaľ pod 40 % stretnutí'}
          </div>
        </div>
      </div>

      ${leagueTabsHtml}
      ${selectedLeagueDetailHtml}

      <div style="margin-top:16px;">
        <div class="signup-group-title">Klikni na súťaž pre detailný prehľad hráča</div>
        <div class="table-card">
          <table>
            <thead>
              <tr>
                <th>Súťaž / Družstvo</th>
                <th>Koef.</th>
                <th>Stretnutia družstva</th>
                <th>Zápasy</th>
                <th>Výhry : Prehry</th>
                <th>Úspešnosť</th>
                <th>Rebríček klubu (≥ 40 %)</th>
              </tr>
            </thead>
            <tbody>
              ${perLeagueRows || '<tr><td colspan="7">Zatiaľ žiadne odohrané zápasy.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}

function renderAll() {
  updateNavigation();
  renderCompetitions();
  renderSignupTab();
  renderLeaderboard();
  renderProfile();
}

document.getElementById("login-form").addEventListener("submit", submitLogin);
document.getElementById("login-password-toggle").addEventListener("click", toggleLoginPasswordVisibility);
document.getElementById("password-form").addEventListener("submit", submitPasswordChange);
document.getElementById("password-dialog").addEventListener("cancel", event => {
  if (currentUser?.mustChangePassword) event.preventDefault();
});

loadData();