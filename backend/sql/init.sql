-- MySQL init script: business tables + stock tables
-- Database should exist (or set MYSQL_DATABASE=stock_analysis in docker-compose).

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Business tables
-- ----------------------------

CREATE TABLE IF NOT EXISTS users (
  id          INT PRIMARY KEY AUTO_INCREMENT,
  username    VARCHAR(50) UNIQUE NOT NULL,
  password    VARCHAR(255) NOT NULL,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS conversations (
  id          INT PRIMARY KEY AUTO_INCREMENT,
  user_id     INT NOT NULL,
  title       VARCHAR(200) DEFAULT '新会话',
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_conversations_user (user_id),
  CONSTRAINT fk_conversations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS messages (
  id              INT PRIMARY KEY AUTO_INCREMENT,
  conversation_id INT NOT NULL,
  role            ENUM('user', 'assistant') NOT NULL,
  content         TEXT NOT NULL,
  extra           JSON,
  content_type    ENUM('text', 'chart', 'table') DEFAULT 'text',
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_messages_conversation (conversation_id),
  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dashboard_widgets (
  id          INT PRIMARY KEY AUTO_INCREMENT,
  user_id     INT NOT NULL,
  title       VARCHAR(200) DEFAULT '未命名',
  type        ENUM('chart', 'table') NOT NULL,
  data        JSON NOT NULL,
  layout      JSON NOT NULL,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_widgets_user (user_id),
  CONSTRAINT fk_widgets_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------
-- Stock tables
-- ----------------------------

CREATE TABLE IF NOT EXISTS stock_code_list (
  ts_code     VARCHAR(20) NOT NULL,
  ak_code     VARCHAR(16) NOT NULL,
  stock_name  VARCHAR(128) NOT NULL,
  update_time DATETIME DEFAULT NULL,
  PRIMARY KEY (ts_code),
  KEY idx_stock_code_list_ak (ak_code),
  KEY idx_stock_code_list_name (stock_name(64))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS stock_daily (
  stock_name  VARCHAR(128) NOT NULL,
  ts_code     VARCHAR(20) NOT NULL,
  trade_date  DATE NOT NULL,
  open        FLOAT DEFAULT NULL,
  high        FLOAT DEFAULT NULL,
  low         FLOAT DEFAULT NULL,
  close       FLOAT DEFAULT NULL,
  pre_close   FLOAT DEFAULT NULL,
  change_val  FLOAT DEFAULT NULL,
  pct_chg     FLOAT DEFAULT NULL,
  vol         DOUBLE DEFAULT NULL,
  amount      DOUBLE DEFAULT NULL,
  PRIMARY KEY (ts_code, trade_date),
  KEY idx_trade_date_cover (trade_date, ts_code, close, pct_chg, vol, amount),
  KEY idx_stock_daily_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET FOREIGN_KEY_CHECKS = 1;

