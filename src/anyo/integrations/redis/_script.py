"""Atomic, non-destructive job storage for a standalone Redis endpoint.

The script stages and encodes every changed record before the first write. Redis
Lua errors do not roll back preceding writes, so key types, schema, definitions,
and capacity are checked before committing the staged changes. Sorted sets index
eligibility, leases, terminal retention, and insertion order; the job hash always
retains a claimed job until its terminal history expires.
"""

SCRIPT = r"""
local types = {'string', 'hash', 'zset', 'zset', 'zset', 'zset', 'hash'}
for i, key in ipairs(KEYS) do
    local actual = redis.call('TYPE', key).ok
    if actual ~= 'none' and actual ~= types[i] then
        return {'mismatch', 'key type'}
    end
end
local signature, operation = ARGV[1], ARGV[2]
local options = cjson.decode(ARGV[3])
local stored = redis.call('GET', KEYS[1])
if not stored then
    if operation ~= 'open' then return {'mismatch', 'missing metadata'} end
    for i = 2, #KEYS do
        if redis.call('EXISTS', KEYS[i]) ~= 0 then
            return {'mismatch', 'orphaned records'}
        end
    end
    redis.call('SET', KEYS[1], signature)
    stored = signature
end
if stored ~= signature then return {'mismatch', 'registry or settings'} end

-- ACL errors, like other Lua errors, cannot roll back preceding writes. Check
-- every mutating command before changing a job. Redis 7's ACL predicate checks
-- the precise command/key pair, including ACL selectors. Redis 6.2 has global
-- command permissions and one key-pattern set (TYPE above checked every key).
-- On 6.2, valid-arity wrong-type commands against metadata prove command grants
-- without changing anything; an allowed command must produce WRONGTYPE.
if redis.acl_check_cmd then
    if not redis.acl_check_cmd('HSET', KEYS[2], 'probe', 'value')
        or not redis.acl_check_cmd('HDEL', KEYS[2], 'probe') then
        return {'permission'}
    end
    for i = 3, 6 do
        if not redis.acl_check_cmd('ZADD', KEYS[i], 0, 'probe')
            or not redis.acl_check_cmd('ZREM', KEYS[i], 'probe') then
            return {'permission'}
        end
    end
else
    local checks = {
        redis.pcall('HSET', KEYS[1], 'probe', 'value'),
        redis.pcall('HDEL', KEYS[1], 'probe'),
        redis.pcall('ZADD', KEYS[1], 0, 'probe'),
        redis.pcall('ZREM', KEYS[1], 'probe'),
    }
    for _, result in ipairs(checks) do
        if type(result) ~= 'table' or type(result.err) ~= 'string'
            or not string.find(result.err, 'WRONGTYPE', 1, true) then
            return {'permission'}
        end
    end
end
if operation == 'open' then return {'ok'} end

local config = cjson.decode(signature)
local timestamp = redis.call('TIME')
local now = tonumber(timestamp[1]) + tonumber(timestamp[2]) / 1000000
local updates, removed, cache = {}, {}, {}
local invalid = false
local revision_sequence = 0
local function new_revision()
    revision_sequence = revision_sequence + 1
    return ARGV[4] .. ':' .. tostring(revision_sequence)
end
local function read(id)
    if removed[id] then return nil end
    if updates[id] then return updates[id] end
    if cache[id] then return cache[id] end
    local raw = redis.call('HGET', KEYS[2], id)
    if not raw then return nil end
    local ok, record = pcall(cjson.decode, raw)
    if not ok or type(record) ~= 'table' or record.id ~= id
        or type(record.definition) ~= 'string' or type(record.job) ~= 'table'
        or record.job.id ~= id or type(record.job.max_attempts) ~= 'number'
        or type(record.job.retry_delay) ~= 'number'
        or type(record.attempt) ~= 'number' or type(record.created_at) ~= 'number'
        or type(record.sequence) ~= 'number'
        or type(record.available_at) ~= 'number'
        or not ({queued=true, running=true, successful=true, failed=true,
                 interrupted=true, cancelled=true})[record.status]
        or (record.status == 'running' and (type(record.lease_until) ~= 'number'
            or type(record.token) ~= 'string')) then
        invalid = true
        return nil
    end
    -- Additive upgrade for queued records created by an earlier release.
    if record.status == 'queued' and not record.token then
        record.token = new_revision()
        updates[id] = record
    end
    cache[id] = record
    return record
end
local function delay(record)
    if record.job.retry_delay == 0 then return 0 end
    local exponent = math.log(record.job.retry_delay) + (record.attempt - 1) * math.log(2)
    if exponent >= math.log(3600) then return 3600 end
    return math.exp(exponent)
end
local function terminal(record, status, error)
    record.status = status
    record.error = error
    record.completed_at = now
    record.token, record.owner, record.lease_until = nil, nil, nil
end
local function enqueue(record, available)
    record.status, record.available_at = 'queued', available
    record.completed_at, record.owner, record.lease_until = nil, nil, nil
    record.token = new_revision()
end

local active = redis.call('ZCARD', KEYS[3]) + redis.call('ZCARD', KEYS[4])
local count = redis.call('HLEN', KEYS[2])
for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[5], '-inf',
                              now - config.retention_seconds)) do
    local record = read(id)
    if not record or (record.status ~= 'successful' and record.status ~= 'failed'
                      and record.status ~= 'interrupted' and record.status ~= 'cancelled') then
        return {'mismatch', 'terminal index'}
    end
    removed[id], count = true, count - 1
end
for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[4], '-inf', now)) do
    local record = read(id)
    if not record or record.status ~= 'running' then
        return {'mismatch', 'lease index'}
    end
    if record.control then
        terminal(record, 'interrupted', 'anyo.cancel_unconfirmed')
        active = active - 1
    elseif record.job.retry_interrupted and record.attempt < record.job.max_attempts then
        enqueue(record, now + delay(record))
        record.error = 'anyo.lease_expired'
    else
        terminal(record, 'interrupted', 'anyo.lease_expired')
        active = active - 1
    end
    updates[id] = record
end

local response = {'ok'}
if operation == 'admit' then
    local seen, candidates, added = {}, {}, 0
    local newest = redis.call('ZREVRANGE', KEYS[6], 0, 0, 'WITHSCORES')
    local sequence = #newest > 0 and tonumber(newest[2]) or 0
    for _, item in ipairs(options.jobs) do
        local job, definition = item.job, item.definition
        if config.registry[job.hook_id] ~= job.fingerprint then
            return {'mismatch', 'hook fingerprint'}
        end
        if seen[job.id] and seen[job.id] ~= definition then
            return {'mismatch', 'duplicate definition'}
        end
        if not seen[job.id] then
            seen[job.id] = definition
            local record = read(job.id)
            if record and record.definition ~= definition then
                return {'mismatch', 'job definition'}
            end
            if not record or ((record.status == 'failed' or record.status == 'interrupted')
                              and not record.control) then
                if not record then count = count + 1 end
                local created = record and record.created_at or now
                local original_job = record and record.job or job
                local position = record and record.sequence or sequence + 1
                sequence = math.max(sequence, position)
                record = {id=job.id, definition=definition, job=original_job, created_at=created,
                          sequence=position, attempt=0, status='queued', available_at=now,
                          token=new_revision()}
                candidates[job.id] = record
                added, active = added + 1, active + 1
            end
        end
    end
    if active > config.capacity then return {'capacity', 'active jobs'} end
    if count > config.max_records then return {'capacity', 'retained records'} end
    for id, record in pairs(candidates) do
        removed[id] = nil
        updates[id] = record
    end
    response = {'ok', added}
elseif operation == 'claim' then
    local candidate = nil
    local earliest = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now,
                               'WITHSCORES', 'LIMIT', 0, 1)
    if #earliest > 0 then
        for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[3], earliest[2], earliest[2])) do
            local record = read(id)
            if not record then return {'mismatch', 'queued index'} end
            if not candidate or record.sequence < candidate.sequence then candidate = record end
        end
    end
    -- Recovery may have made a job eligible without writing its index yet.
    for _, record in pairs(updates) do
        if record.status == 'queued' and record.available_at <= now
            and (not candidate or record.available_at < candidate.available_at
                or (record.available_at == candidate.available_at
                    and record.sequence < candidate.sequence)) then
            candidate = record
        end
    end
    local raw_worker = redis.call('HGET', KEYS[7], options.owner)
    if not raw_worker then
        local parent = string.match(options.owner, '^(.*):[^:]+$')
        if parent then raw_worker = redis.call('HGET', KEYS[7], parent) end
    end
    if raw_worker then
        local worker = cjson.decode(raw_worker)
        if (worker.command and worker.command ~= cjson.null) or worker.state ~= 'running' then
            candidate = nil
        end
    end
    if candidate then
        if candidate.status ~= 'queued' then return {'mismatch', 'queued index'} end
        candidate.status, candidate.token, candidate.owner = 'running', options.token, options.owner
        candidate.attempt = candidate.attempt + 1
        candidate.started_at = now
        candidate.lease_until, candidate.error = now + options.lease_seconds, nil
        updates[candidate.id] = candidate
        response = {'ok', cjson.encode(candidate)}
    end
elseif operation == 'renew' or operation == 'finish' then
    local record = read(options.id)
    response = {'ok', 0}
    if record and record.status == 'running' and record.token == options.token
        and record.attempt == options.attempt and record.lease_until > now then
        if operation == 'renew' then
            record.lease_until = now + options.lease_seconds
        elseif options.retry and options.status ~= 'successful'
            and options.status ~= 'cancelled' and not record.control
            and record.attempt < record.job.max_attempts
            and (options.status ~= 'interrupted' or record.job.retry_interrupted) then
            enqueue(record, now + delay(record))
            record.error = options.error
        else
            terminal(record, options.status, options.error)
            active = active - 1
        end
        updates[record.id] = record
        response = {'ok', 1}
    end
elseif operation == 'retry' then
    local record = read(options.id)
    response = {'ok', 0}
    if record and (record.status == 'failed' or record.status == 'interrupted'
                   or record.status == 'cancelled') then
        if active >= config.capacity then return {'capacity', 'active jobs'} end
        record.attempt, record.error = 0, nil
        record.control, record.control_requested_at, record.started_at = nil, nil, nil
        enqueue(record, now)
        updates[record.id] = record
        response = {'ok', 1}
    end
elseif operation == 'cancel' then
    local record = read(options.id)
    response = {'ok', 0}
    if record and (record.status == 'queued' or record.status == 'running'
                   or record.status == 'cancelled')
        and (options.expected_token == cjson.null or options.expected_token == nil
             or options.expected_token == (record.token or '')) then
        if record.status ~= 'cancelled' then
            record.control = (options.force or record.control == 'force_stop')
                             and 'force_stop' or 'cancel'
            record.control_requested_at = record.control_requested_at or now
            if record.status == 'queued' then
                terminal(record, 'cancelled', nil)
                active = active - 1
            end
            updates[record.id] = record
        end
        response = {'ok', 1}
    end
elseif operation == 'control' then
    local record = read(options.id)
    if record and record.status == 'running' and record.token == options.token
        and record.attempt == options.attempt and record.lease_until > now and record.control then
        response = {'ok', record.control}
    end
elseif operation == 'inspect' then
    for _, id in ipairs(redis.call('ZRANGE', KEYS[6], 0, -1)) do
        if #response > options.limit then break end
        local record = read(id)
        if record then table.insert(response, cjson.encode(record)) end
    end
else
    return {'mismatch', 'operation'}
end
if invalid then return {'mismatch', 'invalid record'} end

-- Validate all encodings before committing: a script error has no rollback.
local encoded = {}
for id, record in pairs(updates) do encoded[id] = cjson.encode(record) end
for id, _ in pairs(removed) do
    redis.call('HDEL', KEYS[2], id)
    for i = 3, 6 do redis.call('ZREM', KEYS[i], id) end
end
for id, raw in pairs(encoded) do
    local record = updates[id]
    redis.call('HSET', KEYS[2], id, raw)
    for i = 3, 5 do redis.call('ZREM', KEYS[i], id) end
    redis.call('ZADD', KEYS[6], record.sequence, id)
    if record.status == 'queued' then
        redis.call('ZADD', KEYS[3], record.available_at, id)
    elseif record.status == 'running' then
        redis.call('ZADD', KEYS[4], record.lease_until, id)
    else
        redis.call('ZADD', KEYS[5], record.completed_at, id)
    end
end
return response
"""

# Operations metadata is independent of queue capacity/registry fingerprints.
OPERATIONS_SCRIPT = r"""
local types = {'string', 'hash', 'zset', 'zset', 'zset', 'zset',
               'hash', 'zset', 'hash', 'zset', 'zset', 'string', 'string'}
for i, key in ipairs(KEYS) do
    local actual = redis.call('TYPE', key).ok
    if actual ~= 'none' and actual ~= types[i] then return {'mismatch', 'key type'} end
end
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return {'mismatch', 'namespace'} end
local operation, options = ARGV[2], cjson.decode(ARGV[3])
local settings = options.settings
local timestamp = redis.call('TIME')
local now = tonumber(timestamp[1]) + tonumber(timestamp[2]) / 1000000
-- Check every write before pruning or updating; Lua cannot roll back ACL failures.
local checks = {
    {'HSET', KEYS[2], 'probe', 'value'},
    {'HSET', KEYS[7], 'probe', 'value'}, {'HDEL', KEYS[7], 'probe'},
    {'HSET', KEYS[9], 'probe', 'value'}, {'HDEL', KEYS[9], 'probe'},
    {'SET', KEYS[12], '0'}, {'SET', KEYS[13], '0'},
}
for _, i in ipairs({8, 10, 11}) do
    table.insert(checks, {'ZADD', KEYS[i], 0, 'probe'})
    table.insert(checks, {'ZREM', KEYS[i], 'probe'})
end
if redis.acl_check_cmd then
    for _, args in ipairs(checks) do
        if not redis.acl_check_cmd(unpack(args)) then return {'permission'} end
    end
else
    -- Redis 6.2 uses global command grants and one key-pattern set. TYPE checked
    -- each key; wrong-type probes validate H*/Z* without changing metadata.
    for _, args in ipairs({{'HSET',KEYS[1],'probe','value'}, {'HDEL',KEYS[1],'probe'},
                           {'ZADD',KEYS[1],0,'probe'}, {'ZREM',KEYS[1],'probe'}}) do
        local result = redis.pcall(unpack(args))
        if type(result) ~= 'table' or not result.err
            or not string.find(result.err, 'WRONGTYPE', 1, true) then return {'permission'} end
    end
    -- SET permission is necessarily needed to create this namespace initially.
    -- It is checked again against the unchanged metadata before other writes.
    local result = redis.pcall('SET', KEYS[1], ARGV[1])
    if type(result) == 'table' and result.err then return {'permission'} end
end
local expired_workers = redis.call('ZRANGEBYSCORE', KEYS[8], '-inf',
                                   now - settings.worker_retention_seconds)
local expired_set = {}
for _, id in ipairs(expired_workers) do expired_set[id] = true end
local worker_write, worker_id
local job_updates = {}
local response = {'ok'}
local function read_worker(id)
    if expired_set[id] then return nil end
    local raw = redis.call('HGET', KEYS[7], id)
    if not raw then return nil end
    return cjson.decode(raw)
end
if operation == 'worker_heartbeat' then
    local worker = options.worker
    local previous = read_worker(worker.id)
    worker.command = previous and previous.command or cjson.null
    worker.heartbeat_at = now
    worker_write, worker_id = cjson.encode(worker), worker.id
elseif operation == 'workers' then
    for _, id in ipairs(redis.call('ZRANGE', KEYS[8], 0, -1)) do
        local worker = read_worker(id)
        if worker then table.insert(response, cjson.encode(worker)) end
    end
elseif operation == 'worker_command' then
    local worker = read_worker(options.id)
    response = {'ok', 0}
    if worker and worker.state ~= 'stopped' then
        worker.command = (options.force or worker.command == 'force_stop')
                         and 'force_stop' or 'drain'
        worker_write, worker_id = cjson.encode(worker), worker.id
        if options.force then
            local prefix = worker.id .. ':'
            for _, id in ipairs(redis.call('ZRANGE', KEYS[4], 0, -1)) do
                local record = cjson.decode(redis.call('HGET', KEYS[2], id))
                if record.status ~= 'running' then return {'mismatch', 'running index'} end
                if record.owner == worker.id or (type(record.owner) == 'string'
                    and string.sub(record.owner, 1, #prefix) == prefix) then
                    record.control = 'force_stop'
                    record.control_requested_at = record.control_requested_at or now
                    job_updates[id] = cjson.encode(record)
                end
            end
        end
        response = {'ok', 1}
    end
elseif operation == 'get_worker_command' then
    local worker = read_worker(options.id)
    if worker and worker.command and worker.command ~= cjson.null then
        response = {'ok', worker.command}
    end
elseif operation ~= 'append_logs' and operation ~= 'logs' and operation ~= 'logged_tasks' then
    return {'mismatch', 'operation'}
end

local cursor = tonumber(redis.call('GET', KEYS[12]) or '0')
local pruned = tonumber(redis.call('GET', KEYS[13]) or '0')
local additions = {}
if operation == 'append_logs' then
    for _, entry in ipairs(options.entries) do
        cursor = cursor + 1
        entry.cursor = cursor
        if entry.run_id and entry.run_id ~= cjson.null
            and (not entry.hook_id or entry.hook_id == cjson.null) then
            local job = redis.call('HGET', KEYS[2], entry.run_id)
            if job then entry.hook_id = cjson.decode(job).job.hook_id end
        end
        table.insert(additions, {id=tostring(cursor), raw=cjson.encode(entry)})
    end
end
local cutoff = math.max(pruned, cursor - settings.log_max_entries)
for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[11], '-inf',
                              now - settings.log_retention_seconds)) do
    cutoff = math.max(cutoff, tonumber(id))
end
local removed_logs = redis.call('ZRANGEBYSCORE', KEYS[10], '-inf', cutoff)
if operation == 'logged_tasks' then
    local tasks = {}
    for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[10], '(' .. cutoff, '+inf')) do
        local entry = cjson.decode(redis.call('HGET', KEYS[9], id))
        if entry.run_id and entry.run_id ~= cjson.null and entry.run_id ~= '' then
            local previous = tasks[entry.run_id]
            tasks[entry.run_id] = {run_id=entry.run_id,
                entry_count=(previous and previous.entry_count or 0) + 1,
                last_cursor=entry.cursor, last_timestamp=entry.timestamp,
                hook_id=entry.hook_id or cjson.null}
        end
    end
    for _, task in pairs(tasks) do table.insert(response, cjson.encode(task)) end
end
if operation == 'logs' then
    local entries = {}
    local oldest = redis.call('ZRANGEBYSCORE', KEYS[10], '(' .. cutoff, '+inf', 'LIMIT', 0, 1)
    local next_cursor = math.max(options.after, cursor)
    for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[10],
                                   '(' .. math.max(options.after, cutoff), '+inf')) do
        local entry = cjson.decode(redis.call('HGET', KEYS[9], id))
        local matched = true
        for _, field in ipairs({'run_id', 'worker_id', 'source', 'level'}) do
            if options[field] and options[field] ~= cjson.null
                and entry[field] ~= options[field] then matched = false end
        end
        if matched then
            table.insert(entries, entry)
            if #entries >= options.limit then next_cursor = entry.cursor; break end
        end
    end
    response = {'ok', cjson.encode({entries=entries, next_cursor=next_cursor,
        oldest_cursor=(#oldest > 0 and tonumber(oldest[1]) or cjson.null),
        gap=options.after < cutoff})}
end
-- All records and responses are encoded before the first state mutation.
for id, raw in pairs(job_updates) do redis.call('HSET', KEYS[2], id, raw) end
for _, id in ipairs(expired_workers) do
    redis.call('HDEL', KEYS[7], id)
    redis.call('ZREM', KEYS[8], id)
end
if worker_write then
    redis.call('HSET', KEYS[7], worker_id, worker_write)
    if operation == 'worker_heartbeat' then redis.call('ZADD', KEYS[8], now, worker_id) end
end
for _, id in ipairs(removed_logs) do
    redis.call('HDEL', KEYS[9], id)
    redis.call('ZREM', KEYS[10], id)
    redis.call('ZREM', KEYS[11], id)
end
for _, entry in ipairs(additions) do
    if tonumber(entry.id) > cutoff then
        redis.call('HSET', KEYS[9], entry.id, entry.raw)
        redis.call('ZADD', KEYS[10], tonumber(entry.id), entry.id)
        redis.call('ZADD', KEYS[11], now, entry.id)
    end
end
redis.call('SET', KEYS[12], tostring(cursor))
redis.call('SET', KEYS[13], tostring(cutoff))
return response
"""
