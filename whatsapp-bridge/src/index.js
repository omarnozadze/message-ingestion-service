import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'
import { Boom } from '@hapi/boom'
import pino from 'pino'
import qrcode from 'qrcode-terminal'
import path from 'node:path'
import fs from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const FASTAPI_URL = (process.env.FASTAPI_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const FORWARD_TIMEOUT_MS = Number(process.env.FORWARD_TIMEOUT_MS || '30000')
const STATUS_POLL_INTERVAL_MS = Number(process.env.STATUS_POLL_INTERVAL_MS || '1000')
const STATUS_POLL_MAX_MS = Number(process.env.STATUS_POLL_MAX_MS || '3600000')
const BOT_REPLY_PREFIX = '🤖 '


// === whatsapp-command-gate-v43 ===
// Authorized senders can also use this WhatsApp chat for ordinary conversation.
// Only visit-plan commands are forwarded to FastAPI; unrelated text is silently ignored.
const VISIT_COMMAND_DAY_RE = /(ორშაბათ|სამშაბათ|ოთხშაბათ|ხუთშაბათ|პარასკევ|შაბათ|კვირ|დღეს|დღევანდელ)/i
const VISIT_COMMAND_ACTION_RE = /(ჩასვ|ჩამისვ|ჩამიმატ|დაამატ|დამიმატ|დამატებ|დასვ|მონიშნ|გადაიტან|გადმოიტან|შეცვალ|ამოღებ|ამოიღ|ამომიღ|მომიღ|მოხს|ამოშალ|ამომიშალ|მომიშალ|ამომშალ|წაშალ|მხოლოდ|იყოს|იჯდეს)/i
const VISIT_COMMAND_PLAN_RE = /(გეგმიდან|გეგმაში|ყველა\s+დღ)/i
const VISIT_COMMAND_CLIENT_CODE_RE = /(?:^|\s)[#*]?[A-Za-zა-ჰ]?\d{2,6}(?:[._-]\d+)?(?=\s|$|[,;])/i

function looksLikeVisitCommand(text) {
  const value = String(text || '').trim()
  if (!value) return false

  // A visit operation is mandatory. This keeps greetings, location questions,
  // status chat, and other ordinary conversation out of the automation.
  if (!VISIT_COMMAND_ACTION_RE.test(value)) return false

  // Normal add/set/move commands carry a weekday. Remove-all variants may
  // instead say "გეგმიდან" or contain a compact branch/client code.
  return (
    VISIT_COMMAND_DAY_RE.test(value) ||
    VISIT_COMMAND_PLAN_RE.test(value) ||
    VISIT_COMMAND_CLIENT_CODE_RE.test(value)
  )
}
const TERMINAL_STATUSES = new Set(['synced', 'needs_review', 'failed'])

// Test-only mode: allows messages sent by the linked WhatsApp account itself
// to be forwarded as if they came from this phone number.
// Keep disabled in normal/production use.
const ALLOW_SELF_MESSAGES = /^(1|true|yes)$/i.test(process.env.ALLOW_SELF_MESSAGES || '')
const SELF_TEST_SENDER_ID = (process.env.SELF_TEST_SENDER_ID || '').replace(/\D/g, '')

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const AUTH_DIR = path.resolve(__dirname, '..', 'auth')

// === whatsapp-reply-recovery-v45 ===
const STATE_DIR = path.resolve(__dirname, '..', 'state')
const INFLIGHT_FILE = path.join(STATE_DIR, 'inflight-requests.json')
const INFLIGHT_RECOVERY_MAX_AGE_MS = Math.max(STATUS_POLL_MAX_MS * 2, 2 * 60 * 60 * 1000)
let activeSock = null
let recoveredInflightOnce = false
const inflightByExternalId = new Map()

const logger = pino({ level: process.env.LOG_LEVEL || 'warn' })
const seenMessageIds = new Map()
const MAX_SEEN = 5000
const pendingChoicesByPhone = new Map()
const PENDING_CHOICE_TTL_MS = Number(process.env.PENDING_CHOICE_TTL_MS || '600000')

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}


async function persistInflight() {
  try {
    await fs.mkdir(STATE_DIR, { recursive: true })
    const tmp = `${INFLIGHT_FILE}.tmp`
    const rows = [...inflightByExternalId.values()]
    await fs.writeFile(tmp, JSON.stringify(rows, null, 2), 'utf8')
    await fs.rename(tmp, INFLIGHT_FILE)
  } catch (error) {
    console.warn('Could not persist in-flight WhatsApp requests:', error?.message || error)
  }
}

async function trackInflight(record) {
  if (!record?.externalMessageId) return
  inflightByExternalId.set(record.externalMessageId, record)
  await persistInflight()
}

async function clearInflight(externalMessageId) {
  if (!externalMessageId) return
  if (inflightByExternalId.delete(externalMessageId)) {
    await persistInflight()
  }
}

async function loadInflightFromDisk() {
  try {
    const raw = await fs.readFile(INFLIGHT_FILE, 'utf8')
    const rows = JSON.parse(raw)
    if (!Array.isArray(rows)) return []
    const cutoff = Date.now() - INFLIGHT_RECOVERY_MAX_AGE_MS
    const fresh = rows.filter((row) => row?.externalMessageId && Number(row?.createdAt || 0) >= cutoff)
    inflightByExternalId.clear()
    for (const row of fresh) inflightByExternalId.set(row.externalMessageId, row)
    if (fresh.length !== rows.length) await persistInflight()
    return fresh
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      console.warn('Could not load in-flight WhatsApp requests:', error?.message || error)
    }
    return []
  }
}

function rememberMessage(id) {
  if (!id) return true
  if (seenMessageIds.has(id)) return false
  seenMessageIds.set(id, Date.now())
  if (seenMessageIds.size > MAX_SEEN) {
    const oldest = seenMessageIds.keys().next().value
    if (oldest) seenMessageIds.delete(oldest)
  }
  return true
}

function unwrapMessage(message) {
  let current = message
  for (let i = 0; i < 5 && current; i += 1) {
    if (current.ephemeralMessage?.message) {
      current = current.ephemeralMessage.message
      continue
    }
    if (current.viewOnceMessage?.message) {
      current = current.viewOnceMessage.message
      continue
    }
    if (current.viewOnceMessageV2?.message) {
      current = current.viewOnceMessageV2.message
      continue
    }
    break
  }
  return current
}

function extractText(message) {
  const msg = unwrapMessage(message)
  if (!msg) return null

  const text =
    msg.conversation ??
    msg.extendedTextMessage?.text ??
    msg.imageMessage?.caption ??
    msg.videoMessage?.caption ??
    msg.documentMessage?.caption ??
    null

  if (typeof text !== 'string') return null
  const trimmed = text.trim()
  return trimmed || null
}

function jidToPhone(jid) {
  if (typeof jid !== 'string' || !jid.endsWith('@s.whatsapp.net')) return null
  const user = jid.split('@', 1)[0].split(':', 1)[0].replace(/\D/g, '')
  return user || null
}

function extractSenderPhone(key) {
  const candidates = [
    key?.remoteJidAlt,
    key?.participantAlt,
    key?.participantPn,
    key?.senderPn,
    key?.remoteJid,
    key?.participant,
  ]

  for (const candidate of candidates) {
    const phone = jidToPhone(candidate)
    if (phone) return phone
  }
  return null
}

function isSupportedChat(key) {
  const jid = key?.remoteJid || ''
  if (jid.endsWith('@g.us')) return false
  if (jid.endsWith('@broadcast')) return false
  if (jid.endsWith('@newsletter')) return false
  return true
}

async function sendReply(sock, replyJid, text) {
  if (!replyJid || !text) return false

  // A long 1C request can outlive a temporary WhatsApp reconnect. Always send
  // through the newest open socket instead of the socket captured at request start.
  const deadline = Date.now() + 30000
  let lastError = null

  while (Date.now() < deadline) {
    const targetSock = activeSock || sock
    if (!targetSock) {
      await sleep(1000)
      continue
    }

    try {
      const sent = await targetSock.sendMessage(replyJid, {
        text: `${BOT_REPLY_PREFIX}${text}`,
      })
      if (sent?.key?.id) {
        // Prevent a self-test bridge from feeding its own reply back into FastAPI.
        rememberMessage(sent.key.id)
      }
      return true
    } catch (error) {
      lastError = error
      console.warn('WhatsApp reply attempt failed; waiting for reconnect:', error?.message || error)
      await sleep(1000)
    }
  }

  console.error('WhatsApp reply failed after reconnect wait:', lastError?.message || lastError || 'no active socket')
  return false
}

function isClientMatchReview(result) {
  if (String(result?.status || '').toLowerCase() !== 'needs_review') return false
  const error = String(result?.error_message || '').toLowerCase()
  return (
    error.includes('multiple possible clients') ||
    error.includes('client not found') ||
    error.includes('multiple candidates') ||
    error.includes('ambiguous') ||
    error.includes('similar client')
  )
}

async function fetchClientChoices(messageId) {
  if (!messageId) return null
  try {
    const response = await fetch(
      `${FASTAPI_URL}/api/v1/incoming-message/${encodeURIComponent(messageId)}/choices`,
      { signal: AbortSignal.timeout(15000) },
    )
    if (!response.ok) return null
    return await response.json()
  } catch (error) {
    console.warn('Client choices request failed:', error?.message || error)
    return null
  }
}

function choicePrompt(result, choiceData) {
  const choices = Array.isArray(choiceData?.choices) ? choiceData.choices : []
  const error = String(result?.error_message || '').toLowerCase()
  const lines = []

  if (error.includes('client not found')) {
    lines.push('❌ ზუსტი კლიენტი აქტიურ ბაზაში ვერ მოიძებნა.')
    if (choices.length) {
      lines.push('ყველაზე მსგავსი კლიენტები, რომლებიც ვიპოვე:')
    }
  } else if (choices.length === 1) {
    lines.push('⚠️ ერთი შესაძლო კლიენტი მოიძებნა, მაგრამ ავტომატურად უსაფრთხოდ ვერ დავადასტურე. გადაამოწმეთ:')
  } else {
    lines.push('⚠️ რამდენიმე მსგავსი კლიენტი მოიძებნა. აირჩიეთ სწორი ვარიანტი:')
  }

  if (choiceData?.query) {
    lines.push(`ძებნა: ${choiceData.query}`)
  }
  lines.push('')
  choices.forEach((choice, index) => {
    lines.push(`${index + 1}) ${choice.label}`)
  })
  if (choices.length) {
    lines.push('')
    lines.push(`მომწერეთ მხოლოდ ნომერი: 1-${choices.length}`)
    lines.push('ან მომწერეთ ახალი, უფრო ზუსტი მოთხოვნა და ეს არჩევანი გაუქმდება.')
  }
  return lines.join('\n')
}

function parseChoiceNumber(text, max) {
  const trimmed = String(text || '').trim()
  if (!/^\d{1,2}$/.test(trimmed)) return null
  const value = Number(trimmed)
  if (!Number.isInteger(value) || value < 1 || value > max) return null
  return value - 1
}

function getPendingChoice(phone) {
  const pending = pendingChoicesByPhone.get(phone)
  if (!pending) return null
  if (Date.now() - pending.createdAt > PENDING_CHOICE_TTL_MS) {
    pendingChoicesByPhone.delete(phone)
    return null
  }
  return pending
}

async function resolvePendingChoice({ sock, replyJid, phone, choiceIndex }) {
  const pending = getPendingChoice(phone)
  if (!pending) return false
  const choice = pending.choices[choiceIndex]
  if (!choice) return false

  await sendReply(sock, replyJid, `✅ არჩეულია: ${choice.label}. ვასრულებ თავდაპირველ მოთხოვნას...`)

  let result = null
  try {
    const response = await fetch(
      `${FASTAPI_URL}/api/v1/incoming-message/${encodeURIComponent(pending.messageId)}/resolve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: choice.client_id }),
        signal: AbortSignal.timeout(STATUS_POLL_MAX_MS),
      },
    )

    const body = await response.text()
    if (response.ok) {
      result = body ? JSON.parse(body) : null
    } else {
      console.error(`Manual resolve HTTP ${response.status}: ${body.slice(0, 1200)}`)
      result = await waitForFinalStatus(pending.externalMessageId)
    }
  } catch (error) {
    console.warn('Manual resolve request ended:', error?.message || error)
    result = await waitForFinalStatus(pending.externalMessageId)
  }

  if (result && !TERMINAL_STATUSES.has(String(result.status || '').toLowerCase())) {
    result = await waitForFinalStatus(pending.externalMessageId)
  }

  if (result) {
    const reply = buildUserReply(result)
    if (reply) {
      pendingChoicesByPhone.delete(phone)
      await sendReply(sock, replyJid, reply)
      return true
    }
  }

  await sendReply(
    sock,
    replyJid,
    '⚠️ არჩეული კლიენტის საბოლოო სტატუსი ვერ მივიღე. იგივე ნომერი ხელახლა შეგიძლია მომწერო.',
  )
  return true
}

function buildUserReply(result) {
  const status = String(result?.status || '').toLowerCase()
  const error = String(result?.error_message || '').toLowerCase()
  const agent = result?.agent_description ? ` ${result.agent_description}` : ''

  if (status === 'synced') {
    return `✅ წარმატებით შესრულდა.${agent ? ` გეგმა: ${agent.trim()}.` : ''} 1C დოკუმენტი გატარებულია.`
  }

  if (status === 'needs_review') {
    const originalError = String(result?.error_message || '').trim()
    if (originalError.toLowerCase().startsWith('ai review required:')) {
      const detail = originalError.slice('AI review required:'.length).trim()
      if (detail) return `⚠️ ${detail}`
    }
    if (error.includes('client not found')) {
      return '❌ ზუსტი კლიენტი აქტიურ ბაზაში ვერ მოიძებნა. მომწერეთ დასახელება + ფილიალის კოდი/ობიექტის ნომერი ან მისამართი უფრო სრულად.'
    }
    if (error.includes('multiple possible clients')) {
      return '⚠️ რამდენიმე მსგავსი კლიენტი მოიძებნა, მაგრამ ავტომატურად უსაფრთხოდ ვერ ავირჩიე.'
    }
    if (error.includes('unknown sender') || error.includes('sender')) {
      return '⚠️ თქვენი ნომერი აგენტზე ვერ განისაზღვრა. თუ სატესტო ნომერია, შეტყობინება დაიწყეთ აგენტის კოდით.'
    }
    if (error.includes('partner ref')) {
      return '⚠️ კლიენტი მოიძებნა, მაგრამ 1C-ში მის იდენტიფიკატორს დაზუსტება სჭირდება.'
    }
    if (error.includes('plan') || error.includes('posted') || error.includes('document')) {
      return '⚠️ 1C-ის გეგმაში ცვლილება საბოლოოდ ვერ დადასტურდა. საჭიროა შემოწმება.'
    }
    return '⚠️ მოთხოვნა ავტომატურად ვერ შესრულდა. მიუთითეთ კლიენტი და დღე უფრო ზუსტად.'
  }

  if (status === 'failed') {
    return '❌ 1C-ში ჩაწერა ვერ შესრულდა. მოთხოვნა არ ჩაითვალა შესრულებულად.'
  }

  return null
}


async function preflightAuthorized(payload) {
  try {
    const response = await fetch(`${FASTAPI_URL}/api/v1/incoming-message/preflight`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000),
    })

    if (!response.ok) {
      const body = await response.text()
      throw new Error(`preflight HTTP ${response.status}: ${body.slice(0, 500)}`)
    }

    const result = await response.json()
    return result?.authorized === true
  } catch (error) {
    console.warn('FastAPI preflight failed:', error?.message || error)
    return null
  }
}

async function getStatusByExternalId(externalMessageId) {
  const params = new URLSearchParams({
    source: 'whatsapp',
    external_message_id: externalMessageId,
  })
  const response = await fetch(
    `${FASTAPI_URL}/api/v1/incoming-message/by-external-id?${params.toString()}`,
    { signal: AbortSignal.timeout(15000) },
  )

  if (response.status === 404) return null
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`status HTTP ${response.status}: ${body.slice(0, 500)}`)
  }
  return await response.json()
}

async function waitForFinalStatus(externalMessageId) {
  const deadline = Date.now() + STATUS_POLL_MAX_MS
  let lastStatus = null

  while (Date.now() < deadline) {
    try {
      const result = await getStatusByExternalId(externalMessageId)
      if (result) {
        const status = String(result.status || '').toLowerCase()
        if (status && status !== lastStatus) {
          console.log(`… FastAPI poll | status=${status}`)
          lastStatus = status
        }
        if (TERMINAL_STATUSES.has(status)) return result
      }
    } catch (error) {
      console.warn('FastAPI status poll failed:', error?.message || error)
    }
    await sleep(STATUS_POLL_INTERVAL_MS)
  }

  return null
}


async function recoverOneInflight(sock, record) {
  const externalMessageId = record?.externalMessageId
  const replyJid = record?.replyJid
  const phone = record?.phone
  if (!externalMessageId || !replyJid) return

  console.log(`↻ Recovering unfinished WhatsApp request ${externalMessageId}`)

  let result = null
  try {
    result = await getStatusByExternalId(externalMessageId)
    if (!result || !TERMINAL_STATUSES.has(String(result.status || '').toLowerCase())) {
      result = await waitForFinalStatus(externalMessageId)
    }
  } catch (error) {
    console.warn(`Recovery poll failed for ${externalMessageId}:`, error?.message || error)
    return
  }

  if (!result) return

  if (isClientMatchReview(result) && result.id && phone) {
    const choiceData = await fetchClientChoices(result.id)
    const choices = Array.isArray(choiceData?.choices) ? choiceData.choices : []
    if (choices.length > 0) {
      pendingChoicesByPhone.set(phone, {
        messageId: result.id,
        externalMessageId,
        choices,
        createdAt: Date.now(),
      })
      if (await sendReply(sock, replyJid, choicePrompt(result, choiceData))) {
        await clearInflight(externalMessageId)
      }
      return
    }
  }

  const reply = buildUserReply(result)
  if (reply && await sendReply(sock, replyJid, reply)) {
    await clearInflight(externalMessageId)
  }
}

async function recoverInflight(sock) {
  const rows = await loadInflightFromDisk()
  if (!rows.length) return
  console.log(`↻ Recovering ${rows.length} unfinished WhatsApp request(s)`)
  await Promise.allSettled(rows.map((row) => recoverOneInflight(sock, row)))
}

async function forwardToFastApi({ sock, replyJid, id, phone, pushName, text }) {
  const externalMessageId = `baileys:${id}`
  const payload = {
    source: 'whatsapp',
    external_message_id: externalMessageId,
    sender_id: `+${phone}`,
    sender_name: pushName || null,
    text,
  }

  console.log(`→ FastAPI | +${phone} | ${JSON.stringify(text)}`)

  // Never send any WhatsApp reply until FastAPI confirms that this sender is
  // authorized for an agent. Unknown phone numbers are silently ignored.
  const authorized = await preflightAuthorized(payload)
  if (authorized === false) {
    console.log(`↷ Ignoring unauthorized sender +${phone}; no WhatsApp reply sent.`)
    return
  }
  if (authorized !== true) {
    console.warn(`↷ Sender validation unavailable for +${phone}; no WhatsApp reply sent.`)
    return
  }

  await sendReply(sock, replyJid, '⏳ მოთხოვნა მიღებულია, ვამუშავებ...')

  await trackInflight({
    externalMessageId,
    replyJid,
    phone,
    createdAt: Date.now(),
  })

  let result = null

  try {
    const response = await fetch(`${FASTAPI_URL}/api/v1/incoming-message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(FORWARD_TIMEOUT_MS),
    })

    const body = await response.text()
    if (!response.ok) {
      console.error(`FastAPI HTTP ${response.status}: ${body.slice(0, 1500)}`)
      // A 5xx or disconnected response can still mean the long 1C operation is
      // continuing server-side. Poll by the idempotent external message id.
      if (response.status >= 500) {
        result = await waitForFinalStatus(externalMessageId)
      }
    } else {
      try {
        result = JSON.parse(body)
      } catch {
        console.log(`✓ FastAPI HTTP ${response.status}`)
      }
    }
  } catch (error) {
    console.warn(
      `FastAPI request ended before a final response: ${error?.message || error}`,
    )
    console.log('… HTTP wait ended; polling stored status while 1C may still be finishing')
    result = await waitForFinalStatus(externalMessageId)
  }

  if (result && !TERMINAL_STATUSES.has(String(result.status || '').toLowerCase())) {
    console.log(`… FastAPI accepted request; polling until final status`)
    result = await waitForFinalStatus(externalMessageId)
  }

  if (result) {
    console.log(
      `✓ FastAPI | status=${result.status} | 1C=${result.one_c_record_ref || '-'}`,
    )
    if (result.error_message) {
      console.error(`  error=${result.error_message}`)
    }

    if (isClientMatchReview(result) && result.id) {
      const choiceData = await fetchClientChoices(result.id)
      const choices = Array.isArray(choiceData?.choices) ? choiceData.choices : []
      if (choices.length > 0) {
        pendingChoicesByPhone.set(phone, {
          messageId: result.id,
          externalMessageId,
          choices,
          createdAt: Date.now(),
        })
        if (await sendReply(sock, replyJid, choicePrompt(result, choiceData))) {
          await clearInflight(externalMessageId)
        }
        return
      }
    }

    const reply = buildUserReply(result)
    if (reply) {
      if (await sendReply(sock, replyJid, reply)) {
        await clearInflight(externalMessageId)
      }
      return
    }
  }

  const warningSent = await sendReply(
    sock,
    replyJid,
    '⚠️ მოთხოვნა მიღებულია, მაგრამ საბოლოო სტატუსის მიღება ვერ მოხერხდა. 1C-ში ხელით ნუ გაიმეორებთ, სანამ სტატუსს არ გადაამოწმებთ.',
  )
  if (warningSent) await clearInflight(externalMessageId)
}

async function connect() {
  console.log(`Auth directory: ${AUTH_DIR}`)
  console.log(
    `Self-test mode: ${ALLOW_SELF_MESSAGES ? 'ON' : 'OFF'}` +
      (ALLOW_SELF_MESSAGES && SELF_TEST_SENDER_ID ? ` (+${SELF_TEST_SENDER_ID})` : ''),
  )

  if (ALLOW_SELF_MESSAGES && !SELF_TEST_SENDER_ID) {
    throw new Error(
      'ALLOW_SELF_MESSAGES is enabled but SELF_TEST_SENDER_ID is empty. ' +
        'Set SELF_TEST_SENDER_ID=+995555000999',
    )
  }

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

  const sock = makeWASocket({
    auth: state,
    logger,
    markOnlineOnConnect: false,
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      console.log('\nScan this QR code with the CENTRAL WhatsApp account:')
      console.log('WhatsApp → Settings → Linked devices → Link a device\n')
      qrcode.generate(qr, { small: true })
    }

    if (connection === 'open') {
      activeSock = sock
      console.log('\n✓ WhatsApp connected. Waiting for messages...\n')
      if (!recoveredInflightOnce) {
        recoveredInflightOnce = true
        void recoverInflight(sock).catch((error) => {
          console.error('In-flight recovery failed:', error?.message || error)
        })
      }
    }

    if (connection === 'close') {
      if (activeSock === sock) activeSock = null
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut
      console.log(`WhatsApp connection closed. reconnect=${shouldReconnect}`)

      if (shouldReconnect) {
        setTimeout(() => {
          connect().catch((error) => console.error('Reconnect failed:', error))
        }, 2000)
      } else {
        console.error('WhatsApp session was logged out. Delete whatsapp-bridge/auth and scan QR again.')
      }
    }
  })

  sock.ev.on('messages.upsert', ({ type, messages }) => {
    if (type !== 'notify') return

    for (const message of messages || []) {
      const key = message?.key
      if (!key || !isSupportedChat(key)) continue

      const isSelf = Boolean(key.fromMe)
      if (isSelf && !ALLOW_SELF_MESSAGES) continue

      const id = key.id
      if (!rememberMessage(id)) continue

      const text = extractText(message.message)
      if (!text) {
        console.log(`Ignoring non-text message id=${id || '-'}`)
        continue
      }

      // In self-test mode the bridge also sees its own WhatsApp replies. Never
      // forward those back into FastAPI or it would create a reply loop.
      if (isSelf && text.startsWith(BOT_REPLY_PREFIX)) continue

      let phone
      if (isSelf) {
        // In test mode we intentionally force the linked user's real phone
        // because WhatsApp may expose the self-chat with a LID instead of a PN JID.
        phone = SELF_TEST_SENDER_ID
        console.log(`SELF TEST message detected → using +${phone}`)
      } else {
        phone = extractSenderPhone(key)
      }

      if (!phone) {
        console.warn(
          `Could not resolve sender phone for message id=${id || '-'}; ` +
            `remoteJid=${key.remoteJid || '-'} ` +
            `remoteJidAlt=${key.remoteJidAlt || '-'}`,
        )
        continue
      }

      const replyJid = key.remoteJid || `${phone}@s.whatsapp.net`

      const pendingChoice = getPendingChoice(phone)
      if (pendingChoice) {
        const selectedIndex = parseChoiceNumber(text, pendingChoice.choices.length)
        if (selectedIndex !== null) {
          void resolvePendingChoice({
            sock,
            replyJid,
            phone,
            choiceIndex: selectedIndex,
          }).catch((error) => {
            console.error('Choice resolution failed:', error?.message || error)
          })
          continue
        }
      }

      // Unrelated chat from an authorized sender must be silent: no FastAPI
      // request, no "request received" acknowledgement, and no review warning.
      if (!looksLikeVisitCommand(text)) {
        console.log(`↷ Ignoring non-command WhatsApp text from +${phone}`)
        continue
      }

      // A real new command replaces an unanswered old client-choice prompt.
      if (pendingChoice) {
        pendingChoicesByPhone.delete(phone)
      }

      // Do not serialize by phone here. In self-test mode one phone can send
      // commands for several agent prefixes (111/112/402/403). The FastAPI
      // backend serializes writes by the resolved agent/plan, which is the
      // correct concurrency boundary.
      void forwardToFastApi({
        sock,
        replyJid,
        id: id || `${phone}:${Date.now()}`,
        phone,
        pushName: message.pushName,
        text,
      }).catch((error) => {
        console.error('Forward task failed:', error?.message || error)
      })
    }
  })
}

process.on('unhandledRejection', (error) => {
  console.error('Unhandled rejection:', error)
})

connect().catch((error) => {
  console.error('Bridge startup failed:', error)
  process.exitCode = 1
})
