-- ============================================
-- Lost & Found Bot - Database Schema
-- ============================================

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(100),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    lang VARCHAR(5) DEFAULT 'ar',
    points INT DEFAULT 0,
    total_reports INT DEFAULT 0,
    successful_returns INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_users_user_id ON users(user_id);

CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    report_number SERIAL,
    type VARCHAR(10) NOT NULL CHECK (type IN ('lost', 'found')),
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(50),
    description TEXT NOT NULL,
    location_city VARCHAR(50),
    time_range VARCHAR(50),
    photo_file_id VARCHAR(200),
    contact_method VARCHAR(20),
    contact_value VARCHAR(100),
    status VARCHAR(20) DEFAULT 'active',
    views_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_items_type ON items(type);
CREATE INDEX idx_items_status ON items(status);
CREATE INDEX idx_items_category ON items(category);
CREATE INDEX idx_items_city ON items(location_city);
CREATE INDEX idx_items_user ON items(user_id);

CREATE TABLE IF NOT EXISTS matches (
    id BIGSERIAL PRIMARY KEY,
    lost_item_id BIGINT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    found_item_id BIGINT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    score INT NOT NULL,
    match_level VARCHAR(20),
    lost_user_notified BOOLEAN DEFAULT FALSE,
    found_user_notified BOOLEAN DEFAULT FALSE,
    lost_user_confirmed BOOLEAN DEFAULT FALSE,
    found_user_confirmed BOOLEAN DEFAULT FALSE,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(lost_item_id, found_item_id)
);

CREATE INDEX idx_matches_lost ON matches(lost_item_id);
CREATE INDEX idx_matches_found ON matches(found_item_id);
