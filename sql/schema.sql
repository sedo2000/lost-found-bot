-- ============================================
-- Lost & Found Bot - Complete Schema v5.0
-- ============================================

-- ============ 1. المستخدمون ============
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(100),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    lang VARCHAR(5) DEFAULT 'ar',
    points INT DEFAULT 0,
    trust_level INT DEFAULT 1,
    total_reports INT DEFAULT 0,
    successful_returns INT DEFAULT 0,
    average_rating FLOAT DEFAULT 0,
    ratings_count INT DEFAULT 0,
    notify_city VARCHAR(50),
    notify_category VARCHAR(50),
    notify_all BOOLEAN DEFAULT FALSE,
    is_banned BOOLEAN DEFAULT FALSE,
    is_verified BOOLEAN DEFAULT FALSE,
    last_active TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active DESC);

-- ============ 2. البلاغات ============
CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    report_number SERIAL UNIQUE,
    type VARCHAR(10) NOT NULL CHECK (type IN ('lost', 'found')),
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(50),
    description TEXT NOT NULL,
    tags TEXT[],
    location_city VARCHAR(50),
    location_area VARCHAR(100),
    time_range VARCHAR(50),
    time_details VARCHAR(200),
    photos TEXT[],
    is_urgent BOOLEAN DEFAULT FALSE,
    contact_method VARCHAR(20),
    contact_value VARCHAR(100),
    status VARCHAR(20) DEFAULT 'active',
    views_count INT DEFAULT 0,
    matches_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP,
    expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL '60 days')
);

CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_city ON items(location_city);
CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);

-- ============ 3. التطابقات ============
CREATE TABLE IF NOT EXISTS matches (
    id BIGSERIAL PRIMARY KEY,
    lost_item_id BIGINT NOT NULL,
    found_item_id BIGINT NOT NULL,
    score INT NOT NULL,
    match_level VARCHAR(20),
    lost_user_notified BOOLEAN DEFAULT FALSE,
    found_user_notified BOOLEAN DEFAULT FALSE,
    lost_user_confirmed BOOLEAN DEFAULT FALSE,
    found_user_confirmed BOOLEAN DEFAULT FALSE,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP,
    UNIQUE(lost_item_id, found_item_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_lost ON matches(lost_item_id);
CREATE INDEX IF NOT EXISTS idx_matches_found ON matches(found_item_id);

-- ============ 4. التحذيرات ============
CREATE TABLE IF NOT EXISTS warnings (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    reason VARCHAR(200),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings(user_id);

-- ============ 5. المحظورون ============
CREATE TABLE IF NOT EXISTS banned_users (
    user_id BIGINT PRIMARY KEY,
    reason VARCHAR(200),
    banned_at TIMESTAMP DEFAULT NOW()
);

-- ============ 6. الرسائل المباشرة ============
CREATE TABLE IF NOT EXISTS direct_messages (
    id BIGSERIAL PRIMARY KEY,
    from_user_id BIGINT NOT NULL,
    to_user_id BIGINT NOT NULL,
    item_id BIGINT,
    match_id BIGINT,
    message TEXT,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dm_to ON direct_messages(to_user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_dm_from ON direct_messages(from_user_id);

-- ============ 7. غرف الدردشة ============
CREATE TABLE IF NOT EXISTS chats (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT,
    user1_id BIGINT NOT NULL,
    user2_id BIGINT NOT NULL,
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW(),
    closed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chats_users ON chats(user1_id, user2_id);
CREATE INDEX IF NOT EXISTS idx_chats_status ON chats(status);

-- ============ 8. رسائل الدردشة ============
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    from_user_id BIGINT NOT NULL,
    message_text TEXT,
    file_id VARCHAR(200),
    message_type VARCHAR(20) DEFAULT 'text',
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, created_at DESC);

-- ============ 9. الاشتراك الإجباري ============
CREATE TABLE IF NOT EXISTS mandatory_channels (
    id BIGSERIAL PRIMARY KEY,
    channel_id VARCHAR(100) NOT NULL UNIQUE,
    channel_name VARCHAR(200),
    channel_url VARCHAR(300),
    added_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mandatory_channels ON mandatory_channels(channel_id);

-- ============ 10. رسالة Start المخصصة ============
CREATE TABLE IF NOT EXISTS custom_start (
    id BIGSERIAL PRIMARY KEY,
    message_text TEXT,
    photo_file_id VARCHAR(200),
    video_file_id VARCHAR(200),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============ 11. الإذاعة ============
CREATE TABLE IF NOT EXISTS broadcasts (
    id BIGSERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    message_text TEXT,
    media_type VARCHAR(20),
    media_file_id VARCHAR(200),
    pin_message BOOLEAN DEFAULT FALSE,
    sent_count INT DEFAULT 0,
    failed_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============ 12. حالة المستخدم ============
CREATE TABLE IF NOT EXISTS user_states (
    user_id BIGINT PRIMARY KEY,
    state VARCHAR(100),
    data JSONB,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_states ON user_states(user_id);

-- ============ 13. الإشعارات ============
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    type VARCHAR(30) NOT NULL,
    title VARCHAR(200),
    message TEXT,
    is_read BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);

-- ============ 14. الإحصائيات اليومية ============
CREATE TABLE IF NOT EXISTS daily_stats (
    date DATE PRIMARY KEY,
    total_lost INT DEFAULT 0,
    total_found INT DEFAULT 0,
    total_matches INT DEFAULT 0,
    total_resolved INT DEFAULT 0,
    new_users INT DEFAULT 0
);

INSERT INTO daily_stats (date) VALUES (CURRENT_DATE)
ON CONFLICT (date) DO NOTHING;

-- ============ تحقق نهائي ============
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public'
ORDER BY table_name;
