export interface HelpLink {
  label: string
  to: string
}

export interface HelpReply {
  title?: string
  paragraphs: string[]
  bullets?: string[]
  links?: HelpLink[]
  followUps: string[]
}

interface HelpArticle {
  id: string
  title: string
  keywords: string[]
  reply: Omit<HelpReply, 'followUps'> & { followUps?: string[] }
}

const DEFAULT_FOLLOW_UPS = [
  'How do I get started?',
  'How do I search my clips?',
  'How do I make a TikTok reel?',
  'How do I name faces?',
]

const ARTICLES: HelpArticle[] = [
  {
    id: 'start',
    title: 'First-run golden path',
    keywords: [
      'start', 'started', 'begin', 'setup', 'onboard', 'first', 'new', 'empty',
      'library', 'how', 'use', 'tutorial', 'guide', 'intro', 'hello',
    ],
    reply: {
      title: 'Get MediaForge working in four moves',
      paragraphs: [
        'This is a local cutting room: nothing leaves this machine unless you export it. The useful loop is scan → wait for AI → ask in Search or Studio.',
      ],
      bullets: [
        'Settings → add an absolute folder like /home/bfam/Videos → Scan folders.',
        'Let the job tray finish. Thumbnails come first; captions, faces, and transcripts land as the AI worker catches up.',
        'Library should fill. Click a card to inspect GPS, faces, and captions.',
        'Edit Studio: “make a highlight reel for tiktok of my trip to edmonton 9:16”. Review, approve, render.',
      ],
      links: [
        { label: 'Open Settings & Scan', to: '/settings' },
        { label: 'Open Edit Studio', to: '/studio' },
      ],
      followUps: [
        'What folders can I scan?',
        'Why is my library empty?',
        'How do I make a TikTok reel?',
      ],
    },
  },
  {
    id: 'scan',
    title: 'Scan folders',
    keywords: [
      'scan', 'folder', 'folders', 'directory', 'ingest', 'index', 'import',
      'path', 'media', 'watch', 'watcher', 'add',
    ],
    reply: {
      title: 'Point it at real folders, then scan',
      paragraphs: [
        'Paths must be absolute on this machine (they start with /). Relative paths and ~ are rejected. After you add folders, tap Scan folders — MediaForge stores those dirs so the next scan does not depend on .env.',
      ],
      bullets: [
        'Supported: jpg, jpeg, png, heic, heif, mov, mp4, m4v, mkv, avi, webm.',
        'Re-scans skip files MediaForge already hashed. New files in watched folders enqueue automatically if the watcher is on.',
        'GPS on iPhone videos comes from Keys:GPSCoordinates (ISO 6709), e.g. +53.5461-113.4938/ for Edmonton.',
      ],
      links: [{ label: 'Go to Settings', to: '/settings' }],
      followUps: ['Why is my library empty?', 'How does place / GPS work?', 'Is this private?'],
    },
  },
  {
    id: 'empty',
    title: 'Empty library',
    keywords: ['empty', 'nothing', 'no', 'assets', 'photos', 'videos', 'missing'],
    reply: {
      title: 'If the grid is blank, it has not indexed yet',
      paragraphs: [
        'The library only shows files that survived a scan. Filters can also hide everything — clear them, or go scan a folder that actually has media.',
      ],
      bullets: [
        'No directories in Settings → add one and scan.',
        'Scan still running → wait; the job tray in the corner shows progress.',
        'Backend offline → start ./scripts/run.sh or the desktop launcher, then refresh.',
      ],
      links: [{ label: 'Scan folders', to: '/settings' }],
    },
  },
  {
    id: 'search',
    title: 'Semantic search',
    keywords: [
      'search', 'find', 'query', 'semantic', 'clip', 'transcript', 'caption',
      'hybrid', 'look',
    ],
    reply: {
      title: 'Ask in plain language',
      paragraphs: [
        'Search fuses CLIP visual similarity, spoken transcripts, captions, and named faces. Type like you would tell an assistant, not like a filename.',
      ],
      bullets: [
        '“beach sunset clips with Kaleb talking”',
        '“vancouver skyline night”',
        '“birthday cake candles”',
      ],
      links: [{ label: 'Open Search', to: '/search' }],
      followUps: ['How do I name faces?', 'How do I filter the library?'],
    },
  },
  {
    id: 'library',
    title: 'Library filters',
    keywords: ['filter', 'sort', 'tag', 'date', 'kind', 'photo', 'video', 'grid'],
    reply: {
      title: 'Narrow the cutting-room shelves',
      paragraphs: [
        'Library is a filterable grid, not a filesystem browser. Combine kind, face, tag, and dates. Sort by taken date, aesthetic score, or when it was added.',
      ],
      links: [{ label: 'Open Library', to: '/' }],
    },
  },
  {
    id: 'faces',
    title: 'Face clusters',
    keywords: ['face', 'faces', 'person', 'people', 'cluster', 'name', 'kaleb', 'who'],
    reply: {
      title: 'Name a cluster once — search it forever',
      paragraphs: [
        'InsightFace groups similar faces. Until you name a cluster it shows up as Person #id. After you name it, Library and Search can filter by that person.',
      ],
      bullets: [
        'Wait until the AI job finishes — clustering happens after embeddings exist.',
        'Open Faces → Name → e.g. Kaleb.',
        'Then Library → Face dropdown, or search “clips with Kaleb talking”.',
      ],
      links: [{ label: 'Open Faces', to: '/faces' }],
    },
  },
  {
    id: 'studio',
    title: 'Edit Studio',
    keywords: [
      'studio', 'edit', 'reel', 'plan', 'planner', 'intent', 'tiktok', 'youtube',
      'highlight', 'cut', 'timeline', 'approve', 'render',
    ],
    reply: {
      title: 'Describe the reel. Approve before it renders.',
      paragraphs: [
        'Studio parses place, platform, ratio, and duration from your sentence. If you name a city and nothing matches, you get an empty plan — it will not silently swap in Vancouver.',
      ],
      bullets: [
        'Say the place: “trip to edmonton”, “vancouver”.',
        'Say the shape: “9:16”, “tiktok” (defaults to 30s), “16:9 for YouTube”.',
        'Review the timeline, drag to reorder, save, then Approve plan & render. That gate is intentional.',
      ],
      links: [{ label: 'Open Edit Studio', to: '/studio' }],
      followUps: [
        'What should I type for Edmonton TikTok?',
        'How do I export to Resolve?',
        'How does place / GPS work?',
      ],
    },
  },
  {
    id: 'edmonton',
    title: 'Edmonton golden-path prompt',
    keywords: ['edmonton', 'yeg', 'alberta', 'golden', 'example', 'prompt'],
    reply: {
      title: 'The prompt the planner is built around',
      paragraphs: [
        'Paste this in Edit Studio after an Edmonton folder has been scanned (clips stamped +53.5461-113.4938/ reverse-geocode to Edmonton):',
      ],
      bullets: [
        'make a highlight reel for tiktok of my trip to edmonton 9:16',
      ],
      links: [{ label: 'Open Edit Studio', to: '/studio' }],
      followUps: ['How does place / GPS work?', 'How do I scan folders?'],
    },
  },
  {
    id: 'place',
    title: 'Places and GPS',
    keywords: [
      'place', 'gps', 'location', 'city', 'geo', 'geocode', 'iso', 'iphone',
      'trip', 'vancouver', 'map',
    ],
    reply: {
      title: 'City is stamped offline, then used to fence the planner',
      paragraphs: [
        'iPhone .MOV location lives in ISO 6709 strings like +53.5461-113.4938/ (Edmonton) and +49.2827-123.1207/ (Vancouver). Reverse geocode is local: curated metros, then an offline gazetteer. No Google, no Nominatim-during-scan.',
      ],
      bullets: [
        'Trips cluster when clips are ≤36h apart and share a city, sit within 80 km, or live in the same folder.',
        'Ask Studio for a city and only that city’s clips are candidates.',
      ],
      links: [{ label: 'Open Library', to: '/' }],
    },
  },
  {
    id: 'command',
    title: 'Command bar and map',
    keywords: [
      'command', 'bar', 'ask', 'reel', 'map', 'places', 'gps', 'leaflet',
      'trip', 'trips', 'edmonton',
    ],
    reply: {
      title: 'Type a reel, or browse the map',
      paragraphs: [
        'The Library command bar is first focus. Reel-like phrasing (tiktok, 9:16, highlight, recap) opens Studio with that intent. Anything else goes to Search.',
      ],
      bullets: [
        'Trip cards sit under the command bar. Make reel plans from a trip without typing the city.',
        'Map uses OpenStreetMap tiles from the places already in your library — no Mapbox token.',
        'Golden-path example: make a highlight reel for tiktok of my trip to edmonton 9:16',
      ],
      links: [
        { label: 'Open Library', to: '/' },
        { label: 'Open Map', to: '/map' },
      ],
    },
  },
  {
    id: 'dedupe',
    title: 'Duplicate review',
    keywords: ['dupe', 'duplicate', 'duplicates', 'dedupe', 'copy', 'copies', 'same', 'hash'],
    reply: {
      title: 'Exact hashes and near stills',
      paragraphs: [
        'Duplicates lists SHA-256 twins and near-duplicate photos. Keep both, or delete B. Originals in your folders are only removed when you choose Delete B.',
      ],
      links: [{ label: 'Open Duplicates', to: '/dedupe' }],
    },
  },
  {
    id: 'export',
    title: 'Render and NLE export',
    keywords: [
      'export', 'resolve', 'davinci', 'fcpxml', 'edl', 'capcut', 'nle',
      'download', 'mp4', 'ffmpeg',
    ],
    reply: {
      title: 'Render an MP4, or hand the cut to an editor',
      paragraphs: [
        'Approve first. FFmpeg then trims, crossfades, loudnorms, crops to ratio, and can burn Whisper captions. Optional music path enables beat-sync.',
      ],
      bullets: [
        'DaVinci Resolve — live timeline, only if Resolve is running with scripting on. A 503 means it is not.',
        'FCPXML / EDL — the reliable interchange path.',
        'CapCut — experimental draft_content.json. Prefer FCPXML if import breaks.',
      ],
      links: [{ label: 'Open Edit Studio', to: '/studio' }],
    },
  },
  {
    id: 'touchup',
    title: 'Photo touch-up',
    keywords: ['touch', 'touchup', 'enhance', 'denoise', 'sharpen', 'levels', 'photo'],
    reply: {
      title: 'Non-destructive stills',
      paragraphs: [
        'Pick a photo, preview a preset, then apply. Output lands under exports/touchup — originals in your library folders are never overwritten.',
      ],
      links: [{ label: 'Open Touch-up', to: '/touchup' }],
    },
  },
  {
    id: 'gpu',
    title: 'GPU, CPU, and Ollama',
    keywords: [
      'gpu', 'cuda', 'cpu', 'vram', 'slow', 'ollama', 'model', 'ai', 'worker',
    ],
    reply: {
      title: 'Local models, loaded lazily',
      paragraphs: [
        'The badge in the sidebar is the truth. CUDA on an RTX 3080 is the happy path. CPU works, it is just slower. Captions need Ollama (qwen2.5vl:7b) on :11434.',
      ],
      bullets: [
        'If captions stall: curl http://127.0.0.1:11434/api/tags — if that fails, start ollama serve.',
        'exiftool missing → GPS/dates stay empty. sudo apt install libimage-exiftool-perl',
      ],
      links: [{ label: 'System status', to: '/settings' }],
    },
  },
  {
    id: 'privacy',
    title: 'Local-first privacy',
    keywords: ['privacy', 'local', 'cloud', 'network', 'lan', 'security', 'private'],
    reply: {
      title: 'Everything stays on this box',
      paragraphs: [
        'Indexing, search, and planning run locally. The backend binds 0.0.0.0:8420 so phones on your LAN can open it — anyone on that network can see the library. There is no login. On an untrusted network, use localhost or firewall 8420.',
      ],
    },
  },
]

const GREETING: HelpReply = {
  title: 'Forge — cutting-room guide',
  paragraphs: [
    'Ask me how to scan, search, name faces, or cut a reel. I stay on this machine and only know MediaForge — no cloud chat.',
  ],
  bullets: [
    'Press ? anytime to open me.',
    'Name a place in Studio or you get an honest empty plan, not a surprise city.',
  ],
  followUps: DEFAULT_FOLLOW_UPS,
}

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((t) => t.length > 1)
}

function isGreeting(q: string): boolean {
  return /^(hi|hey|hello|yo|sup|thanks|thank|help|what can you)\b/.test(q.trim().toLowerCase())
}

export function contextualGreeting(pathname: string): HelpReply {
  const extra: Record<string, string> = {
    '/': 'You are in the Library. If the grid is empty, scan a folder first.',
    '/search': 'Type a sentence, not a filename. Faces only match after you name a cluster.',
    '/faces': 'Name a cluster (Kaleb, Mom, …) and it becomes a Library filter and a search term.',
    '/studio': 'Include a city, a ratio, and a vibe. Approve is the hard gate before FFmpeg.',
    '/map': 'Pins are cities already reverse-geocoded from GPS. Empty map means no geotags yet.',
    '/dedupe': 'Exact pairs share a hash. Near pairs are similar stills. Delete B removes the extra asset.',
    '/touchup': 'Stills only. Presets write a copy under exports/touchup.',
    '/settings': 'Absolute paths, then Scan. Watch the job tray for ingest + AI.',
  }
  const line = extra[pathname]
  if (!line) return GREETING
  return {
    ...GREETING,
    paragraphs: [line, ...GREETING.paragraphs],
  }
}

export function answerHelp(query: string): HelpReply {
  const q = query.trim()
  if (!q || isGreeting(q)) return GREETING

  const tokens = tokenize(q)
  let best: HelpArticle | null = null
  let bestScore = 0
  for (const article of ARTICLES) {
    let score = 0
    const hay = article.keywords
    for (const t of tokens) {
      if (hay.includes(t)) score += 3
      else if (hay.some((k) => k.includes(t) || t.includes(k))) score += 1
    }
    const blob = `${article.title} ${article.keywords.join(' ')}`.toLowerCase()
    if (blob.includes(q.toLowerCase())) score += 8
    if (score > bestScore) {
      bestScore = score
      best = article
    }
  }

  if (!best || bestScore < 3) {
    return {
      title: 'I can walk the cutting room',
      paragraphs: [
        'I did not lock onto that. Try one of the jobs below, or ask about scan, search, faces, Studio, export, GPU, or privacy.',
      ],
      followUps: DEFAULT_FOLLOW_UPS,
    }
  }

  return {
    title: best.reply.title,
    paragraphs: best.reply.paragraphs,
    bullets: best.reply.bullets,
    links: best.reply.links,
    followUps: best.reply.followUps ?? DEFAULT_FOLLOW_UPS,
  }
}

export const HELP_STARTERS = DEFAULT_FOLLOW_UPS
export const HELP_OPEN_EVENT = 'mf-help'
