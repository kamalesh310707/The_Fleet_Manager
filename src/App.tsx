import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bell,
  Bot,
  Box,
  Cloud,
  Crosshair,
  FileText,
  Grid3X3,
  Hand,
  LayoutDashboard,
  MapPin,
  Navigation,
  Package,
  PackageCheck,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Settings,
  Shield,
  ShieldCheck,
  Sparkles,
  Truck,
  User,
  Users,
  Wifi,
  WifiOff,
  Zap,
} from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE ?? (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname || 'localhost'}:8000` : 'http://localhost:8000');
const WS_URL = import.meta.env.VITE_WS_URL ?? (typeof window !== 'undefined' ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.hostname || 'localhost'}:8000/ws/dashboard` : 'ws://localhost:8000/ws/dashboard');
const GRID_SIZE = 30;
const GAUGE_CIRC = 2 * Math.PI * 50;

type RobotStatus = 'idle' | 'moving' | 'picking' | 'placing' | 'error';
type TaskPriority = 'low' | 'normal' | 'high' | 'critical';
type TaskStatus = 'queued' | 'assigned' | 'in_progress' | 'complete' | 'failed';
type TaskType = 'move' | 'pick_place';
type ItemStatus = 'on_shelf' | 'carried' | 'delivered';
type Coordinate = [number, number];

type RobotState = {
  robot_id: string;
  position: Coordinate;
  status: RobotStatus;
  target: Coordinate | null;
  path: Coordinate[];
  path_index: number;
  tasks_completed: number;
  carrying_item_id: string | null;
  phase: string;
  last_seen: number;
};

type ItemState = {
  item_id: string;
  position: Coordinate;
  status: ItemStatus;
  carried_by: string | null;
  sku: string;
};

type Task = {
  task_id: string;
  task_type: TaskType;
  target: Coordinate;
  pick?: Coordinate | null;
  place?: Coordinate | null;
  item_id?: string | null;
  status: TaskStatus;
  assigned_to: string | null;
  priority: TaskPriority;
  phase: string;
};

type FleetMetrics = {
  tasks_dispatched: number;
  tasks_completed: number;
  tasks_failed: number;
  picks_completed: number;
  places_completed: number;
  conflicts_resolved_by_delay: number;
  conflicts_queued: number;
  reservations_active: number;
  robots_idle: number;
  robots_moving: number;
  robots_error: number;
  items_on_shelf: number;
  items_carried: number;
  items_delivered: number;
  avg_path_length: number;
};

type WorldState = {
  robots: Record<string, RobotState>;
  reservations: Record<string, string>;
  task_queue: Task[];
  obstacles: Coordinate[];
  items: Record<string, ItemState>;
  metrics: FleetMetrics;
};

const ROBOT_COLORS: Record<string, string> = {
  robot_1: '#ef4444',
  robot_2: '#22c55e',
  robot_3: '#3b82f6',
  robot_4: '#eab308',
  robot_5: '#a855f7',
};

const PRIORITY_OPTIONS: TaskPriority[] = ['low', 'normal', 'high', 'critical'];

const SCENARIOS: { name: string; blurb: string }[] = [
  { name: 'pick_wave', blurb: '5 pick-and-place jobs across the floor' },
  { name: 'burst', blurb: 'Simultaneous move commands' },
  { name: 'crossing', blurb: 'Crossing paths (reservation test)' },
  { name: 'priority_race', blurb: 'Critical task jumps the queue' },
  { name: 'stress', blurb: 'Sustained mixed traffic' },
];

function rackAisle(x0: number, y0: number, rows: number, cols: number, gap = 1): Coordinate[] {
  const cells: Coordinate[] = [];
  for (let dx = 0; dx < rows; dx++) {
    for (let dy = 0; dy < cols; dy++) {
      if (gap && dy === Math.floor(cols / 2)) continue;
      cells.push([x0 + dx, y0 + dy]);
    }
  }
  return cells;
}

function buildDefaultObstacles(): Coordinate[] {
  const obs: Coordinate[] = [];
  for (const x0 of [4, 8, 12, 16, 20, 24]) {
    obs.push(...rackAisle(x0, 4, 2, 10));
    obs.push(...rackAisle(x0, 16, 2, 10));
  }
  obs.push(...rackAisle(6, 26, 3, 3, 0));
  obs.push(...rackAisle(20, 26, 3, 3, 0));
  return obs;
}

const DEFAULT_OBSTACLES: Coordinate[] = buildDefaultObstacles();

const DEFAULT_PLACE_BAYS: Coordinate[] = [
  [1, 1], [1, 14], [1, 28],
  [14, 1], [28, 1],
  [28, 14], [28, 28], [14, 28],
];

function emptyWorld(): WorldState {
  return {
    robots: {
      robot_1: { robot_id: 'robot_1', position: [1, 1], status: 'idle', target: null, path: [], path_index: 0, tasks_completed: 0, carrying_item_id: null, phase: 'move', last_seen: Date.now() / 1000 },
      robot_2: { robot_id: 'robot_2', position: [1, 15], status: 'idle', target: null, path: [], path_index: 0, tasks_completed: 0, carrying_item_id: null, phase: 'move', last_seen: Date.now() / 1000 },
      robot_3: { robot_id: 'robot_3', position: [1, 28], status: 'idle', target: null, path: [], path_index: 0, tasks_completed: 0, carrying_item_id: null, phase: 'move', last_seen: Date.now() / 1000 },
      robot_4: { robot_id: 'robot_4', position: [28, 1], status: 'idle', target: null, path: [], path_index: 0, tasks_completed: 0, carrying_item_id: null, phase: 'move', last_seen: Date.now() / 1000 },
      robot_5: { robot_id: 'robot_5', position: [28, 28], status: 'idle', target: null, path: [], path_index: 0, tasks_completed: 0, carrying_item_id: null, phase: 'move', last_seen: Date.now() / 1000 },
    },
    reservations: {},
    task_queue: [],
    obstacles: DEFAULT_OBSTACLES,
    items: {},
    metrics: {
      tasks_dispatched: 0,
      tasks_completed: 0,
      tasks_failed: 0,
      picks_completed: 0,
      places_completed: 0,
      conflicts_resolved_by_delay: 0,
      conflicts_queued: 0,
      reservations_active: 0,
      robots_idle: 5,
      robots_moving: 0,
      robots_error: 0,
      items_on_shelf: 0,
      items_carried: 0,
      items_delivered: 0,
      avg_path_length: 0,
    },
  };
}

function cap(s: string) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function App() {
  const [world, setWorld] = useState<WorldState>(emptyWorld());
  const [connected, setConnected] = useState(false);
  const [priority, setPriority] = useState<TaskPriority>('normal');
  const [mode, setMode] = useState<'move' | 'pick_place'>('move');
  const [pickCell, setPickCell] = useState<Coordinate | null>(null);
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [showHeat, setShowHeat] = useState(false);
  const [showPaths, setShowPaths] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<number | null>(null);

  const applyWorld = useCallback((data: WorldState) => setWorld(data), []);

  const fetchWorld = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/world-state`);
      if (!res.ok) return;
      applyWorld((await res.json()) as WorldState);
    } catch {
      /* */
    }
  }, [applyWorld]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let closed = false;
    const connect = () => {
      if (closed) return;
      try {
        const ws = new WebSocket(WS_URL);
        wsRef.current = ws;
        ws.onopen = () => {
          setConnected(true);
          flash('Connected to fleet backend');
        };
        ws.onmessage = (ev) => {
          try {
            applyWorld(JSON.parse(ev.data) as WorldState);
          } catch {
            /* */
          }
        };
        ws.onclose = () => {
          setConnected(false);
          wsRef.current = null;
          if (!closed) reconnectRef.current = window.setTimeout(connect, 1500);
        };
        ws.onerror = () => ws.close();
      } catch {
        reconnectRef.current = window.setTimeout(connect, 1500);
      }
    };
    connect();
    const poll = window.setInterval(() => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) fetchWorld();
    }, 2000);
    return () => {
      closed = true;
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      window.clearInterval(poll);
      wsRef.current?.close();
    };
  }, [applyWorld, fetchWorld]);

  const obstacleSet = useMemo(() => {
    const s = new Set<string>();
    const obs = (world.obstacles && world.obstacles.length > 0) ? world.obstacles : DEFAULT_OBSTACLES;
    for (const [x, y] of obs) s.add(`${x},${y}`);
    return s;
  }, [world.obstacles]);

  const baySet = useMemo(() => {
    const s = new Set<string>();
    for (const [x, y] of DEFAULT_PLACE_BAYS) s.add(`${x},${y}`);
    return s;
  }, []);

  const laneSet = useMemo(() => {
    const s = new Set<string>();
    for (let i = 0; i < GRID_SIZE; i++) {
      for (const y of [3, 15, 26]) s.add(`${i},${y}`);
      for (const x of [3, 15, 26]) s.add(`${x},${i}`);
    }
    return s;
  }, []);

  const robotByCell = useMemo(() => {
    const m = new Map<string, RobotState>();
    for (const r of Object.values(world.robots || {})) {
      m.set(`${r.position[0]},${r.position[1]}`, r);
    }
    return m;
  }, [world.robots]);

  const itemByCell = useMemo(() => {
    const m = new Map<string, ItemState>();
    for (const it of Object.values(world.items || {})) {
      if (it.status === 'on_shelf') m.set(`${it.position[0]},${it.position[1]}`, it);
    }
    return m;
  }, [world.items]);

  const pathCells = useMemo(() => {
    const m = new Map<string, string>();
    if (!showPaths) return m;
    for (const r of Object.values(world.robots || {})) {
      if (!r.path?.length) continue;
      if (!['moving', 'picking', 'placing'].includes(r.status)) continue;
      for (const [x, y] of r.path) m.set(`${x},${y}`, r.robot_id);
    }
    return m;
  }, [world.robots, showPaths]);

  const heatCells = useMemo(() => {
    if (!showHeat) return new Set<string>();
    const s = new Set<string>();
    for (const key of Object.keys(world.reservations || {})) {
      const [x, y] = key.split(',');
      s.add(`${x},${y}`);
    }
    return s;
  }, [showHeat, world.reservations]);

  const robots = useMemo(
    () => Object.values(world.robots || {}).sort((a, b) => a.robot_id.localeCompare(b.robot_id)),
    [world.robots],
  );

  const shelfItems = useMemo(
    () =>
      Object.values(world.items || {})
        .filter((i) => i.status === 'on_shelf')
        .sort((a, b) => a.item_id.localeCompare(b.item_id)),
    [world.items],
  );

  const activeTasks = useMemo(
    () =>
      (world.task_queue || []).filter((t) =>
        ['queued', 'assigned', 'in_progress'].includes(t.status),
      ),
    [world.task_queue],
  );

  const flash = (text: string) => {
    setMessage(text);
    window.setTimeout(() => setMessage(null), 4000);
  };

  const robotOnline = (r: RobotState) => {
    if (!r.last_seen) return false;
    return now - r.last_seen < 8;
  };

  const onCellClick = async (target: Coordinate) => {
    const [x, y] = target;
    if (obstacleSet.has(`${x},${y}`)) {
      flash('That cell is a storage rack');
      return;
    }

    const itemHere = itemByCell.get(`${x},${y}`);

    // Pick & place flow
    if (mode === 'pick_place') {
      if (!pickCell) {
        setPickCell(target);
        setSelectedItem(itemHere ? itemHere.item_id : null);
        flash(
          itemHere
            ? `Locked pick: ${itemHere.sku} at (${x},${y}) — now click a destination cell or bay`
            : `Locked pick at (${x},${y}) — now click a destination cell or bay`
        );
        return;
      }
      if (pickCell[0] === x && pickCell[1] === y) {
        setPickCell(null);
        setSelectedItem(null);
        flash('Pick selection canceled');
        return;
      }
      try {
        const body: Record<string, unknown> = {
          pick: pickCell,
          place: target,
          priority,
        };
        if (selectedItem) body.item_id = selectedItem;
        const res = await fetch(`${API_BASE}/dispatch/pick-place`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) flash(typeof data.detail === 'string' ? data.detail : 'Pick-place failed');
        else flash(data.message ?? `Pick-place dispatched: (${pickCell[0]},${pickCell[1]}) -> (${x},${y})`);
        setPickCell(null);
        setSelectedItem(null);
        fetchWorld();
      } catch {
        flash('Cannot reach backend on :8000');
      }
      return;
    }

    // Default action on empty cells: DISPATCH AGV DRIVE
    try {
      const res = await fetch(`${API_BASE}/dispatch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, priority }),
      });
      const data = await res.json();
      if (!res.ok) flash(typeof data.detail === 'string' ? data.detail : 'Dispatch failed');
      else flash(data.message ?? `AGV dispatched to (${x}, ${y})`);
      fetchWorld();
    } catch {
      flash('Cannot reach backend on :8000');
    }
  };

  const dispatchItem = async (item: ItemState) => {
    setMode('pick_place');
    setPickCell(item.position);
    setSelectedItem(item.item_id);
    flash(`Selected ${item.sku} — click a free place bay on the map`);
  };

  const resetRobot = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/robots/${id}/reset`, { method: 'POST' });
      const data = await res.json();
      flash(data.message ?? `Reset ${id}`);
      fetchWorld();
    } catch {
      flash('Reset failed');
    }
  };

  const runScenario = async (name: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/scenarios/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!res.ok) flash(data.detail ?? 'Scenario failed');
      else flash(`Scenario “${name}” started (${data.results?.length ?? 0} jobs)`);
      fetchWorld();
    } catch {
      flash('Backend unreachable');
    } finally {
      setBusy(false);
    }
  };

  const m = world.metrics || emptyWorld().metrics;
  const onlineCount = robots.filter(robotOnline).length;

  const fleetHealthScore = Math.max(0, Math.min(100, Math.round(
    100 - (m.robots_error * 15) - (m.tasks_failed * 2) + Math.min(10, m.tasks_completed)
  )));
  const healthPct = robots.length ? onlineCount / Math.max(robots.length, 1) : 0;

  const timelineHeights = [40, 55, 35, 70, 45, 80, 60, 50, 75, 42, 65, 55, 30, 48, 72, 38, 58, 45, 68, 52];
  const sparkHeights = [30, 45, 25, 60, 40, 70, 55, 35, 65, 50, 45, 80, 40, 55, 35, 60, 50, 70, 45, 55];
  const loadBars = [20, 35, 50, 40, 60, 45, 70, 55, 40, 65, 50, 75, 45, 55, 35, 60];

  return (
    <div className="soc-shell">
      <aside className="soc-sidebar">
        <div className="soc-sidebar-logo">
          <Truck size={18} strokeWidth={2.4} />
        </div>
        <button type="button" className="soc-nav-btn active" title="Dashboard">
          <LayoutDashboard size={18} />
        </button>
        <button type="button" className="soc-nav-btn" title="Alerts">
          <Bell size={18} />
        </button>
        <button type="button" className="soc-nav-btn" title="Fleet">
          <Bot size={18} />
        </button>
        <button type="button" className="soc-nav-btn" title="Map">
          <Grid3X3 size={18} />
        </button>
        <button type="button" className="soc-nav-btn" title="Security">
          <Shield size={18} />
        </button>
        <button type="button" className="soc-nav-btn" title="Team">
          <Users size={18} />
        </button>
        <button type="button" className="soc-nav-btn" title="Cloud">
          <Cloud size={18} />
        </button>
        <button type="button" className="soc-nav-btn" title="Reports">
          <FileText size={18} />
        </button>
        <div className="soc-sidebar-spacer" />
        <button type="button" className="soc-nav-btn" title="Settings">
          <Settings size={18} />
        </button>
      </aside>

      <div className="soc-main">
        <header className="soc-topbar">
          <div className="soc-filter">
            <span>Team:</span>
            <select defaultValue="global">
              <option value="global">Global Fleet</option>
              <option value="warehouse">Warehouse Ops</option>
            </select>
          </div>
          <div className="soc-filter">
            <span>Region:</span>
            <select defaultValue="apac">
              <option value="apac">Asia Pacific</option>
              <option value="global">Global</option>
            </select>
          </div>
          <div className="soc-filter">
            <span>Time:</span>
            <select defaultValue="24h">
              <option value="24h">Last 24 Hours</option>
              <option value="1h">Last Hour</option>
              <option value="7d">Last 7 Days</option>
            </select>
          </div>
          <div className="soc-filter">
            <span>Mode:</span>
            <select
              value={mode}
              onChange={(e) => {
                const v = e.target.value as 'move' | 'pick_place';
                setMode(v);
                setPickCell(null);
                if (v === 'move') setSelectedItem(null);
              }}
            >
              <option value="move">Move only</option>
              <option value="pick_place">Pick &amp; Place</option>
            </select>
          </div>
          <div className="soc-filter">
            <span>Priority:</span>
            <select value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
              {PRIORITY_OPTIONS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>

          <div className={`soc-live-pill ${connected ? 'on' : 'off'}`}>
            <span className="soc-pulse" />
            {connected ? 'Backend live' : 'Connecting…'}
          </div>
          <div className={`soc-live-pill ${onlineCount > 0 ? 'on' : 'off'}`}>
            {onlineCount > 0 ? <Wifi size={12} /> : <WifiOff size={12} />}
            {onlineCount}/{robots.length || 5} AGVs
          </div>

          <div className="soc-search">
            <Search size={14} />
            <input type="text" placeholder="Search by Name etc..." />
          </div>
          <div className="soc-avatar">
            <User size={16} />
          </div>
        </header>

        {message && <div className="soc-toast">{message}</div>}

        <div className="soc-content">
          <div className="soc-page-header">
            <div className="soc-header-icon">
              <LayoutDashboard size={18} />
            </div>
            <div>
              <h1>Fleet Operations Center</h1>
              <p>Real-Time Fleet Intelligence &amp; Incident Monitoring · 5 AGVs · 30×30 · <span style={{ color: 'var(--cyan)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>layout-v7</span></p>
            </div>
          </div>

          {mode === 'pick_place' && (
            <div className="soc-banner">
              <Package size={16} />
              {pickCell ? (
                <span>
                  <strong>Step 2/2</strong> — Pick locked at ({pickCell[0]}, {pickCell[1]})
                  {selectedItem ? ` · ${selectedItem}` : ''}. Click a <strong>free place bay</strong> on the map.
                  <button type="button" className="soc-linkish" onClick={() => { setPickCell(null); setSelectedItem(null); }}>
                    Cancel
                  </button>
                </span>
              ) : (
                <span>
                  <strong>Step 1/2</strong> — Click a shelf item or empty cell for pick location.
                </span>
              )}
            </div>
          )}

          <div className="soc-body">
            <div className="soc-left">
              <div className="soc-card soc-timeline">
                <div className="soc-card-head">
                  <span className="soc-card-title">Task Activity Timeline</span>
                  <button type="button" className="soc-card-action"><RefreshCw size={12} /></button>
                </div>
                <div className="soc-chart-bars">
                  {timelineHeights.map((h, i) => (
                    <div key={i} className="soc-chart-bar" style={{ height: `${h}%` }} />
                  ))}
                </div>
                <div className="soc-chart-labels">
                  <span>00</span>
                  <span>06</span>
                  <span>12</span>
                  <span>18</span>
                </div>
              </div>

              <div className="soc-card" style={{ flex: '1 1 40%', minHeight: 0 }}>
                <div className="soc-card-head">
                  <span className="soc-card-title">Fleet AGVs <span className="soc-count">{robots.length}</span></span>
                </div>
                <div className="soc-robot-list">
                  {robots.length === 0 && (
                    <div className="soc-empty">
                      No AGVs yet.
                      <code>python -m robots.run_fleet_viewer</code>
                    </div>
                  )}
                  {robots.map((r) => {
                    const online = robotOnline(r);
                    return (
                      <div key={r.robot_id} className={`soc-robot-row ${!online ? 'offline' : ''}`}>
                        <div
                          className="soc-robot-avatar"
                          style={{ background: ROBOT_COLORS[r.robot_id] ?? '#64748b' }}
                        >
                          <Bot size={18} />
                        </div>
                        <div className="soc-robot-meta">
                          <strong>
                            {r.robot_id}
                            <span className={`soc-badge ${r.status === 'error' ? 'error' : r.status === 'idle' ? 'idle' : 'moving'}`}>
                              {cap(r.status)}
                            </span>
                          </strong>
                          <span>
                            ({r.position[0]}, {r.position[1]})
                            {r.target ? ` → (${r.target[0]}, ${r.target[1]})` : ''}
                            {r.carrying_item_id ? ` · ${r.carrying_item_id}` : ''}
                          </span>
                        </div>
                        <button type="button" className="soc-icon-btn" title="Reset" onClick={() => resetRobot(r.robot_id)}>
                          <RotateCcw size={13} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="soc-card" style={{ flex: '1 1 30%', minHeight: 0 }}>
                <div className="soc-card-head">
                  <span className="soc-card-title">Scenarios</span>
                </div>
                <div className="soc-scenarios">
                  {SCENARIOS.map(({ name, blurb }) => (
                    <button
                      key={name}
                      type="button"
                      className="soc-scenario-btn"
                      disabled={busy}
                      onClick={() => runScenario(name)}
                    >
                      <Play size={16} style={{ flexShrink: 0, marginTop: 2, color: 'var(--cyan)' }} />
                      <div>
                        <strong>{name}</strong>
                        <span>{blurb}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="soc-card" style={{ flex: '1 1 30%', minHeight: 0 }}>
                <div className="soc-card-head">
                  <span className="soc-card-title">Active Jobs <span className="soc-count">{activeTasks.length}</span></span>
                </div>
                <div className="soc-task-list">
                  {activeTasks.length === 0 && <div className="soc-empty">No active jobs</div>}
                  {activeTasks.slice(0, 8).map((t) => (
                    <div key={t.task_id} className="soc-task-row">
                      <div className={`soc-prio ${t.priority}`}>{t.priority}</div>
                      <div>
                        <strong>
                          {t.task_type === 'pick_place' ? 'PICK→PLACE' : 'MOVE'}{' '}
                          {t.task_type === 'pick_place'
                            ? `${t.pick ? `(${t.pick[0]},${t.pick[1]})` : '?'} → (${t.target[0]},${t.target[1]})`
                            : `→ (${t.target[0]}, ${t.target[1]})`}
                        </strong>
                        <span>
                          {t.status}
                          {t.assigned_to ? ` · ${t.assigned_to}` : ' · unassigned'}
                          {t.phase ? ` · ${t.phase}` : ''}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="soc-center">
              <div className="soc-map-panel">
                <div className="soc-map-glow" />
                <div className="soc-grid-wrap">
                  <div className="soc-grid">
                    {Array.from({ length: GRID_SIZE * GRID_SIZE }, (_, i) => {
                      const x = i % GRID_SIZE;
                      const y = Math.floor(i / GRID_SIZE);
                      const key = `${x},${y}`;
                      const isObstacle = obstacleSet.has(key);
                      const isBay = baySet.has(key);
                      const isLane = laneSet.has(key);
                      const isHeat = heatCells.has(key);
                      const robot = robotByCell.get(key);
                      const item = itemByCell.get(key);
                      const pathOwner = pathCells.get(key);
                      const cls = [
                        'soc-cell',
                        isObstacle ? 'obstacle' : '',
                        isBay && !isObstacle ? 'bay' : '',
                        isLane && !isObstacle && !isBay ? 'lane' : '',
                        isHeat ? 'heat' : '',
                        pathOwner ? 'path' : '',
                      ].filter(Boolean).join(' ');
                      return (
                        <button
                          key={key}
                          type="button"
                          className={cls}
                          disabled={isObstacle}
                          title={
                            robot
                              ? `${robot.robot_id} · ${robot.status}${robot.carrying_item_id ? ' · carrying ' + robot.carrying_item_id : ''} @ (${x},${y})`
                              : item
                                ? `Item ${item.sku} (${item.item_id}) @ (${x},${y})`
                                : isBay
                                  ? `Dock / Shipping Bay (${x},${y})`
                                  : `Floor Cell (${x},${y})`
                          }
                          style={
                            pathOwner
                              ? ({ '--path-color': ROBOT_COLORS[pathOwner] ?? '#94a3b8' } as React.CSSProperties)
                              : robot
                                ? ({ '--robot-color': ROBOT_COLORS[robot.robot_id] ?? '#94a3b8' } as React.CSSProperties)
                                : undefined
                          }
                          onClick={() => onCellClick([x, y])}
                        >
                          {robot && (
                            <span className="soc-robot-dot">
                              <Bot size={9} />
                            </span>
                          )}
                          {!robot && item && <span className="soc-item-dot" />}
                          {!robot && !item && pathOwner && <span className="soc-path-dot" />}
                          {!robot && !item && !pathOwner && isBay && <span className="soc-bay-dot" />}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div className="soc-grid-legend">
                  <span><i className="lg-obstacle" /> Storage Rack</span>
                  <span><i className="lg-bay" /> Dock Bay</span>
                  <span><i className="lg-lane" /> Transit Lane</span>
                  <span><i className="lg-path" /> Path</span>
                  <span><i className="lg-moving" /> AGV</span>
                  <span>● Inventory</span>
                  <div className="soc-toggles">
                    <button type="button" className={`soc-toggle ${showPaths ? 'active' : ''}`} onClick={() => setShowPaths((v) => !v)}>Paths</button>
                    <button type="button" className={`soc-toggle ${showHeat ? 'active' : ''}`} onClick={() => setShowHeat((v) => !v)}>Heat</button>
                  </div>
                </div>
              </div>

              <div className="soc-ask">
                <Sparkles size={16} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
                <input className="soc-ask-input" placeholder="Ask Fleet Intelligence..." readOnly />
                <div className="soc-ask-pills">
                  <button type="button" className={`soc-ask-pill ${mode === 'move' ? 'active' : ''}`} onClick={() => { setMode('move'); setPickCell(null); setSelectedItem(null); }}>
                    <Navigation size={12} /> Move
                  </button>
                  <button type="button" className={`soc-ask-pill ${mode === 'pick_place' ? 'active' : ''}`} onClick={() => { setMode('pick_place'); setPickCell(null); }}>
                    <Hand size={12} /> Pick
                  </button>
                </div>
                <button type="button" className="soc-ask-send" title="Send">
                  <Send size={14} />
                </button>
              </div>
            </div>

            <div className="soc-right">
              <div className="soc-card">
                <div className="soc-card-head">
                  <span className="soc-card-title">Fleet Health Radar</span>
                  <button type="button" className="soc-card-action"><RefreshCw size={12} /></button>
                </div>
                <div className="soc-gauge">
                  <div className="soc-gauge-ring">
                    <svg className="soc-gauge-svg" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
                      {/* Track + dark core */}
                      <circle cx="60" cy="60" r="55" fill="rgba(6,10,15,0.55)" />
                      <circle cx="60" cy="60" r="50" stroke="rgba(30,58,79,0.8)" strokeWidth="9" />
                      {/* Progress ring (starts at 12 o'clock) - driven by online AGV ratio */}
                      {healthPct > 0 && (
                        <circle
                          cx="60"
                          cy="60"
                          r="50"
                          stroke="#22d3ee"
                          strokeWidth="9"
                          strokeLinecap="round"
                          strokeDasharray={`${healthPct * GAUGE_CIRC} ${GAUGE_CIRC}`}
                          transform="rotate(-90 60 60)"
                          style={{ filter: 'drop-shadow(0 0 5px #22d3ee88)' }}
                        />
                      )}
                      {/* Tick mark at 12 o'clock */}
                      <line x1="60" y1="2" x2="60" y2="14" stroke="rgba(34,211,238,0.55)" strokeWidth="1.5" strokeLinecap="round" />
                    </svg>
                    <div className="soc-gauge-value">{onlineCount || robots.length || 5}</div>
                  </div>
                  <div className="soc-gauge-label">Active AGVs</div>
                </div>
              </div>

              <div className="soc-card">
                <div className="soc-card-head">
                  <span className="soc-card-title">Dispatch Volume Trend</span>
                  <button type="button" className="soc-card-action"><RefreshCw size={12} /></button>
                </div>
                <div className="soc-stat-big">{m.tasks_dispatched.toLocaleString()}</div>
                <div className="soc-stat-sub">Tasks · dispatched this session</div>
                <div className="soc-sparkline">
                  {sparkHeights.map((h, i) => (
                    <div key={i} className="soc-spark-bar" style={{ height: `${h}%` }} />
                  ))}
                </div>
              </div>

              <div className="soc-card">
                <div className="soc-card-head">
                  <span className="soc-card-title">Robot Status Overview</span>
                  <button type="button" className="soc-card-action"><RefreshCw size={12} /></button>
                </div>
                <div className="soc-ai-grid">
                  <div className="soc-ai-cell high">
                    <div className="val">{m.robots_error}</div>
                    <div className="lbl">Errors</div>
                  </div>
                  <div className="soc-ai-cell med">
                    <div className="val">{m.conflicts_queued}</div>
                    <div className="lbl">Conflicts</div>
                  </div>
                  <div className="soc-ai-cell low">
                    <div className="val">{m.robots_idle}</div>
                    <div className="lbl">Idle</div>
                  </div>
                  <div className="soc-ai-cell resolved">
                    <div className="val">{m.conflicts_resolved_by_delay}</div>
                    <div className="lbl">Resolved</div>
                  </div>
                </div>
              </div>

              <div className="soc-card">
                <div className="soc-card-head">
                  <span className="soc-card-title">System Confidence</span>
                </div>
                <div className="soc-conf-row">
                  <span className="soc-conf-icon"><Zap size={14} /></span>
                  <span className="soc-conf-label">Path Planning</span>
                  <div className="soc-conf-bar"><div className="soc-conf-fill" style={{ width: '97%' }} /></div>
                  <span className="soc-conf-pct">97%</span>
                </div>
                <div className="soc-conf-row">
                  <span className="soc-conf-icon"><Activity size={14} /></span>
                  <span className="soc-conf-label">Anomaly Detect</span>
                  <div className="soc-conf-bar"><div className="soc-conf-fill" style={{ width: '94%' }} /></div>
                  <span className="soc-conf-pct">94%</span>
                </div>
                <div className="soc-conf-row">
                  <span className="soc-conf-icon"><ShieldCheck size={14} /></span>
                  <span className="soc-conf-label">Conflict Resol.</span>
                  <div className="soc-conf-bar"><div className="soc-conf-fill" style={{ width: '89%' }} /></div>
                  <span className="soc-conf-pct">89%</span>
                </div>
              </div>
            </div>
          </div>

          <div className="soc-bottom">
            <div className="soc-metric">
              <div className="soc-metric-label">Fleet Health Score</div>
              <div className="soc-metric-value">{fleetHealthScore}%</div>
              <div className="soc-metric-delta up">↑ fleet health</div>
            </div>
            <div className="soc-metric">
              <div className="soc-metric-label">Tasks Completed</div>
              <div className="soc-metric-value">{m.tasks_completed.toLocaleString()}</div>
              <div className="soc-metric-delta up">↑ completed</div>
            </div>
            <div className="soc-metric">
              <div className="soc-metric-label">Load Sequence</div>
              <div className="soc-load-chart">
                <div className="soc-load-ring">{activeTasks.length}</div>
                <div className="soc-load-bars">
                  {loadBars.map((h, i) => (
                    <div key={i} className="soc-load-bar" style={{ height: `${h}%` }} />
                  ))}
                </div>
              </div>
            </div>
            <div className="soc-metric">
              <div className="soc-metric-label">Total AGVs</div>
              <div className="soc-metric-value">{(robots.length || 5).toLocaleString()}</div>
              <div className="soc-metric-delta up">↑ AGVs</div>
            </div>
            <div className="soc-metric">
              <div className="soc-metric-label">Active Issues</div>
              <div className="soc-metric-value">{activeTasks.length + m.robots_error}</div>
              <div className={`soc-metric-delta ${m.robots_error > 0 ? 'down' : 'up'}`}>
                {m.robots_error > 0 ? '↓ errors' : '↑ stable'}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;