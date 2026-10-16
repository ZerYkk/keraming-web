const teams = [
  { id: "A", name: "Muži A", league: "Joola Extraliga", coef: 2.50 },
  { id: "B", name: "Muži B", league: "2. liga KSTZ TN", coef: 1.80 },
  { id: "C", name: "Muži C", league: "3. liga KSTZ TN", coef: 1.50 },
  { id: "D", name: "Muži D", league: "4. liga KSTZ TN", coef: 1.25 },
  { id: "E", name: "Muži E", league: "5. liga OSTZ TN", coef: 1.00 },
  { id: "F", name: "Muži F", league: "5. liga OSTZ TN", coef: 1.00 },
  { id: "G", name: "Muži G", league: "6. liga OSTZ TN", coef: 0.85 }
];

let matches = [];
let clubPlayers = [];
let opponentsCache = {};
let currentUserId = "guest";
let activeTeamId = "A";
let activeMainTab = "competitions";
let expandedMatchId = null;

async function loadData() {
  try {
    const resMatches = await fetch("/api/matches");
    matches = await resMatches.json();

    const resPlayers = await fetch("/api/players");
    clubPlayers = await resPlayers.json();

    populateUserSelect();

    if (matches.length > 0) {
      const first = matches.find(m => m.teamId === activeTeamId);
      if (first) {
        toggleMatch(first.id);
      }
    }
  } catch (e) {
    console.error("Chyba pri načítaní dát:", e);
  }

  updateNavigation();
  renderAll();
}

function populateUserSelect() {
  const sel = document.getElementById("user-switcher");
  let html = `<option value="guest">🔒 Neprihlásený návštevník</option>`;
  clubPlayers.forEach(p => {
    html += `<option value="${p.id}">👤 ${p.name} (Súpiska: ${p.roster.join(", ")})</option>`;
  });
  sel.innerHTML = html;
  if (clubPlayers.length > 0 && currentUserId === "guest") {
    currentUserId = clubPlayers[0].id;
    sel.value = String(currentUserId);
  }
}

function getCurrentPlayer() {
  if (currentUserId === "guest") return null;
  return clubPlayers.find(p => p.id === parseInt(currentUserId, 10));
}

function changeUser(val) {
  currentUserId = val === "guest" ? "guest" : parseInt(val, 10);
  if (currentUserId === "guest" && (activeMainTab === "signup" || activeMainTab === "profile")) {
    activeMainTab = "competitions";
  }
  updateNavigation();
  renderAll();
}

function updateNavigation() {
  const isLogged = currentUserId !== "guest" && getCurrentPlayer() !== undefined;
  document.getElementById("nav-signup").classList.toggle("hidden", !isLogged);
  document.getElementById("nav-profile").classList.toggle("hidden", !isLogged);

  ["competitions", "signup", "leaderboard", "profile"].forEach(t => {
    document.getElementById(`tab-${t}`).classList.toggle("hidden", t !== activeMainTab);
    const btn = document.getElementById(`nav-${t}`);
    if (btn) btn.classList.toggle("active", t === activeMainTab);
  });
}

function switchMainTab(tab) {
  activeMainTab = tab;
  updateNavigation();
  renderAll();
}

function selectTeam(teamId) {
  activeTeamId = teamId;
  const first = matches.find(m => m.teamId === teamId);
  expandedMatchId = first ? first.id : null;
  if (expandedMatchId) {
    loadOpponents(expandedMatchId);
  }
  renderAll();
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
  if (opponentsCache[matchId]) return;
  try {
    const res = await fetch(`/api/matches/${matchId}/opponents`);
    opponentsCache[matchId] = await res.json();
  } catch (e) {
    opponentsCache[matchId] = [];
  }
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
  document.getElementById("comp-subtitle").innerText = `Koeficient súťaže pre klubový rebríček: ×${team.coef.toFixed(2)}`;

  const teamMatches = matches.filter(m => m.teamId === activeTeamId);
  const rosterPlayers = clubPlayers.filter(p => p.roster.includes(activeTeamId));

  if (teamMatches.length === 0) {
    document.getElementById("matches-container").innerHTML = `<div class="slot-card">Zatiaľ žiadne stiahnuté zápasy pre toto družstvo.</div>`;
    return;
  }

  document.getElementById("matches-container").innerHTML = teamMatches.map(m => {
    const isExp = expandedMatchId === m.id;
    const opps = opponentsCache[m.id] || [];
    const oppTeam = m.isHome ? m.away : m.home;

    return `
      <div class="match-card ${isExp ? 'expanded' : ''}">
        <div class="match-row" onclick="toggleMatch(${m.id})">
          <div style="display:flex;align-items:center;gap:10px;">
            <span class="${m.isHome ? 'tag-home' : 'tag-away'}">${m.isHome ? 'DOMA' : 'VONKU'}</span>
            <span style="font-size:12px;color:#71717a;"><strong>${m.round}</strong> • ${m.date} • ${m.slot}</span>
          </div>
          <div class="match-teams">${m.home} <span style="color:#71717a;font-weight:400;">vs</span> ${m.away}</div>
          <div>
            ${m.status === 'played' ? `<span class="score-pill">${m.score}</span>` : `<span class="slot-pill">${m.slot}</span>`}
          </div>
        </div>
        ${isExp ? `
          <div class="match-detail">
            <div class="stats-grid">
              <div class="stats-box">
                <div class="stats-box-header">
                  <span>Úspešnosť: Keraming Trenčín (${m.teamId})</span>
                  <span>Dvojhry</span>
                </div>
                ${rosterPlayers.length > 0 ? rosterPlayers.map(p => {
                  const total = p.won + p.lost;
                  const pct = total > 0 ? Math.round((p.won / total) * 100) : 0;
                  return `
                    <div class="player-stat-row">
                      <span class="player-link" onclick="openPlayerProfile(${p.id})">${p.name}</span>
                      <span>${p.won}:${p.lost} (<strong>${pct} %</strong>)</span>
                    </div>
                  `;
                }).join("") : '<div style="font-size:12px;color:#71717a;">Žiadni hráči na súpiske.</div>'}
              </div>

              <div class="stats-box">
                <div class="stats-box-header">
                  <span>Úspešnosť súpera: ${oppTeam}</span>
                  <span>Dvojhry</span>
                </div>
                ${opps.length > 0 ? opps.map(o => {
                  const total = o.won + o.lost;
                  const pct = total > 0 ? Math.round((o.won / total) * 100) : 0;
                  return `
                    <div class="player-stat-row">
                      <span><strong>${o.name}</strong></span>
                      <span>${o.won}:${o.lost} (<strong>${pct} %</strong>)</span>
                    </div>
                  `;
                }).join("") : '<div style="font-size:12px;color:#71717a;padding:8px 0;">Načítavam hráčov súpera zo stolnytenis.info...</div>'}
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
  if (!me) return;

  document.getElementById("my-signup-box").innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div>
        <span style="font-size:15px;font-weight:800;">Moja dochádzka na sobotu: ${me.name}</span>
        <span style="font-size:12px;color:#71717a;margin-left:8px;">(Tvoja súpiska: ${me.roster.map(r => 'Muži ' + r).join(', ')})</span>
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
      <input type="text" class="note-input" value="${me.note || ''}" onchange="updateMyNote(this.value)" placeholder="Poznámka (napr. môžem len 1 zápas / beriem 3 ľudí)...">
      <button class="btn-primary" onclick="saveMySignup()">Uložiť moju voľbu</button>
    </div>
  `;

  document.getElementById("signup-table-body").innerHTML = clubPlayers.map(p => {
    const isMe = p.id === me.id;
    return `
      <tr class="${isMe ? 'me-row' : ''}">
        <td>
          <strong>${p.name}</strong> ${isMe ? '<span style="font-size:11px;font-weight:700;color:#ca8a04;">(Ty)</span>' : ''}
          <div style="font-size:11px;color:#71717a;">Súpiska: ${p.roster.join(", ")}</div>
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
          <span style="font-size:12px;color:#71717a;">${p.note || ''}</span>
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
    await fetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        playerId: me.id,
        canMorning: me.canMorning,
        canAfternoon: me.canAfternoon,
        cannotPlay: me.cannotPlay,
        selectedLeagues: me.selectedLeagues,
        takesCar: me.takesCar,
        note: me.note
      })
    });
    const ind = document.getElementById("save-indicator");
    if (ind) ind.innerText = "✓ Uložené v databáze";
  } catch (e) {}
}

function renderLeaderboard() {
  const list = clubPlayers.map(p => {
    const team = teams.find(t => t.id === p.teamId) || teams[0];
    const total = p.won + p.lost;
    const pct = total > 0 ? Math.round((p.won / total) * 100) : 0;
    const kIndex = +(pct * team.coef).toFixed(1);
    return { ...p, team, pct, kIndex };
  }).sort((a, b) => b.kIndex - a.kIndex);

  document.getElementById("leaderboard-body").innerHTML = list.map((p, i) => `
    <tr style="cursor:pointer;" onclick="openPlayerProfile(${p.id})">
      <td><strong>${i + 1}.</strong></td>
      <td><span class="player-link">${p.name}</span></td>
      <td>${p.team.name} – ${p.team.league} (×${p.team.coef.toFixed(2)})</td>
      <td>${p.won} : ${p.lost}</td>
      <td><strong>${p.pct} %</strong></td>
      <td><strong style="background:#fef9c3;padding:3px 8px;border-radius:6px;border:1px solid #facc15;">${p.kIndex} b</strong></td>
    </tr>
  `).join("");
}

function openMyProfile() {
  const me = getCurrentPlayer();
  if (me) openPlayerProfile(me.id);
}

function openPlayerProfile(playerId) {
  const p = clubPlayers.find(x => x.id === playerId);
  if (!p) return;
  const team = teams.find(t => t.id === p.teamId) || teams[0];
  const total = p.won + p.lost;
  const pct = total > 0 ? Math.round((p.won / total) * 100) : 0;
  const kIndex = +(pct * team.coef).toFixed(1);

  activeMainTab = "profile";
  updateNavigation();

  document.getElementById("profile-container").innerHTML = `
    <div class="my-signup-card">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#71717a;">Karta hráča</div>
      <h2 style="font-size:24px;font-weight:800;margin-top:4px;">${p.name}</h2>
      <p style="font-size:13px;color:#71717a;margin-top:4px;">
        Kmeňové družstvo: <strong>${team.name} (${team.league})</strong> • Súpiska: <strong>${p.roster.map(r => 'Muži ' + r).join(', ')}</strong>
      </p>
      <div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:16px;">
        <div class="slot-card" style="min-width:140px;">
          <div style="font-size:11px;color:#71717a;text-transform:uppercase;font-weight:700;">Dvojhry</div>
          <div style="font-size:20px;font-weight:800;">${p.won} : ${p.lost} (${pct} %)</div>
        </div>
        <div class="slot-card" style="min-width:140px;">
          <div style="font-size:11px;color:#71717a;text-transform:uppercase;font-weight:700;">Štvorhry</div>
          <div style="font-size:20px;font-weight:800;">${p.doubles}</div>
        </div>
        <div class="slot-card" style="min-width:140px;background:#fef9c3;border-color:#facc15;">
          <div style="font-size:11px;color:#854d0e;text-transform:uppercase;font-weight:700;">Klubový K-Index</div>
          <div style="font-size:20px;font-weight:800;">${kIndex} b</div>
        </div>
      </div>
    </div>
  `;
}

function renderAll() {
  renderCompetitions();
  renderSignupTab();
  renderLeaderboard();
}

loadData();