-- Atomic compare-and-set for conversation state (Redis Cluster hash-tag safe).
-- KEYS[1] = state hash key   taksitlio:chat:{sessionId}:state
-- KEYS[2] = idempotency key  taksitlio:chat:{sessionId}:idem:{idemKey}
--
-- ARGV:
-- 1 expected_revision
-- 2 next_revision
-- 3 payload_json
-- 4 schema_version
-- 5 status
-- 6 updated_at
-- 7 expires_at
-- 8 absolute_expires_at
-- 9 last_client_message_id
-- 10 last_client_sequence (empty string if nil)
-- 11 now_epoch_ms
-- 12 idle_ttl_seconds
-- 13 idempotency_ttl_seconds
-- 14 client_message_id
-- 15 request_fingerprint (sha256 of canonical mutation request)
-- 16 idempotency_result_json (to store on success)

local state_key = KEYS[1]
local idem_key = KEYS[2]

local expected_revision = tonumber(ARGV[1])
local next_revision = tonumber(ARGV[2])
local payload = ARGV[3]
local schema_version = ARGV[4]
local status = ARGV[5]
local updated_at = ARGV[6]
local expires_at = ARGV[7]
local absolute_expires_at = ARGV[8]
local last_client_message_id = ARGV[9]
local last_client_sequence = ARGV[10]
local now_ms = tonumber(ARGV[11])
local idle_ttl = tonumber(ARGV[12])
local idem_ttl = tonumber(ARGV[13])
local client_message_id = ARGV[14]
local request_fp = ARGV[15]
local idem_result_json = ARGV[16]

-- Idempotent replay?
if redis.call('EXISTS', idem_key) == 1 then
  local stored_fp = redis.call('HGET', idem_key, 'fingerprint')
  local stored_rev = redis.call('HGET', idem_key, 'revision')
  local stored_result = redis.call('HGET', idem_key, 'result')
  if stored_fp == request_fp then
    return {'IDEMPOTENT_REPLAY', stored_rev or '', stored_result or ''}
  end
  return {'DUPLICATE_PAYLOAD_MISMATCH', stored_rev or '', ''}
end

if redis.call('EXISTS', state_key) == 0 then
  return {'SESSION_NOT_FOUND', '', ''}
end

local cur_rev = tonumber(redis.call('HGET', state_key, 'revision') or '0')
local abs_exp = redis.call('HGET', state_key, 'absolute_expires_at')
local cur_exp = redis.call('HGET', state_key, 'expires_at')
local cur_status = redis.call('HGET', state_key, 'status')
local cur_seq = redis.call('HGET', state_key, 'last_client_sequence')

-- Absolute / idle expiry check using ISO strings compared lexicographically when Zulu,
-- plus status EXPIRED.
if cur_status == 'EXPIRED' or cur_status == 'COMPLETED' or cur_status == 'CANCELLED' then
  return {'SESSION_EXPIRED', tostring(cur_rev), ''}
end

-- Caller also validates clock; script trusts expires_at/absolute_expires_at ISO ordering
-- when both are UTC Zulu. Soft check: if expires_at provided and now marker passed via ARGV.
-- We store epoch helpers when available.
local abs_epoch = tonumber(redis.call('HGET', state_key, 'absolute_expires_at_epoch_ms') or '-1')
local exp_epoch = tonumber(redis.call('HGET', state_key, 'expires_at_epoch_ms') or '-1')
if abs_epoch > 0 and now_ms >= abs_epoch then
  return {'SESSION_EXPIRED', tostring(cur_rev), ''}
end
if exp_epoch > 0 and now_ms >= exp_epoch then
  return {'SESSION_EXPIRED', tostring(cur_rev), ''}
end

if cur_rev ~= expected_revision then
  return {'VERSION_CONFLICT', tostring(cur_rev), ''}
end

-- Sequence check: if both present and incoming < current => OUT_OF_ORDER
-- if equal, treat as ordering gate only when not idempotent (idem already handled)
if last_client_sequence ~= '' and cur_seq and cur_seq ~= '' then
  local incoming = tonumber(last_client_sequence)
  local current = tonumber(cur_seq)
  if incoming and current and incoming < current then
    return {'OUT_OF_ORDER', tostring(cur_rev), ''}
  end
  if incoming and current and incoming == current then
    -- Same sequence without matching idempotency key is conflict/out-of-order
    return {'OUT_OF_ORDER', tostring(cur_rev), ''}
  end
end

if next_revision ~= cur_rev + 1 then
  return {'INVALID_STATE', tostring(cur_rev), ''}
end

redis.call('HSET', state_key,
  'payload', payload,
  'revision', tostring(next_revision),
  'schema_version', schema_version,
  'status', status,
  'updated_at', updated_at,
  'expires_at', expires_at,
  'absolute_expires_at', absolute_expires_at,
  'last_client_message_id', last_client_message_id,
  'last_client_sequence', last_client_sequence,
  'expires_at_epoch_ms', ARGV[17],
  'absolute_expires_at_epoch_ms', ARGV[18]
)

-- Sliding TTL capped by absolute lifetime remaining
local abs_left_ms = abs_epoch - now_ms
if abs_epoch < 0 then
  abs_left_ms = idle_ttl * 1000
end
local ttl_ms = idle_ttl * 1000
if abs_left_ms > 0 and abs_left_ms < ttl_ms then
  ttl_ms = abs_left_ms
end
if ttl_ms < 1000 then
  ttl_ms = 1000
end
redis.call('PEXPIRE', state_key, ttl_ms)

local idem_ttl_ms = idem_ttl * 1000
if idem_ttl_ms < ttl_ms then
  idem_ttl_ms = ttl_ms
end
redis.call('HSET', idem_key,
  'fingerprint', request_fp,
  'revision', tostring(next_revision),
  'result', idem_result_json,
  'client_message_id', client_message_id
)
redis.call('PEXPIRE', idem_key, idem_ttl_ms)

return {'APPLIED', tostring(next_revision), idem_result_json}
