from flask import Flask, jsonify, request
from flask_cors import CORS
import mysql.connector
import os
import decimal
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, auth
from functools import wraps
from .config import get_config

# 載入環境變數
load_dotenv()

# 1. 初始化 Flask 應用程式
app = Flask(__name__)
app.config.from_object(get_config())

# 2. 設定 CORS，允許所有來源的請求
# 這樣你的前端網頁才能成功呼叫後端 API
CORS(app)

# 2.5 初始化 Firebase Admin SDK
try:
    private_key = os.getenv('FIREBASE_PRIVATE_KEY')
    if not private_key:
        raise ValueError("FIREBASE_PRIVATE_KEY 環境變數未設定")

    # 處理私鑰格式
    if private_key.startswith('"') and private_key.endswith('"'):
        private_key = private_key[1:-1]  # 移除首尾的引號
    private_key = private_key.replace('\\n', '\n')  # 將 \n 轉換為實際的換行符

    cred = credentials.Certificate({
        "type": "service_account",
        "project_id": os.getenv('FIREBASE_PROJECT_ID'),
        "private_key_id": os.getenv('FIREBASE_PRIVATE_KEY_ID'),
        "private_key": private_key,
        "client_email": os.getenv('FIREBASE_CLIENT_EMAIL'),
        "client_id": os.getenv('FIREBASE_CLIENT_ID'),
        "auth_uri": os.getenv('FIREBASE_AUTH_URI'),
        "token_uri": os.getenv('FIREBASE_TOKEN_URI'),
        "auth_provider_x509_cert_url": os.getenv('FIREBASE_AUTH_PROVIDER_CERT_URL'),
        "client_x509_cert_url": os.getenv('FIREBASE_CLIENT_CERT_URL'),
        "universe_domain": os.getenv('FIREBASE_UNIVERSE_DOMAIN')
    })
    firebase_admin.initialize_app(cred)
except Exception as e:
    print(f"Firebase 初始化錯誤: {str(e)}")
    print("請確認 .env 檔案已正確設定")
    raise

# 2.6 建立一個裝飾器來驗證 Firebase ID Token
def check_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        id_token = None
        if 'Authorization' in request.headers and request.headers['Authorization'].startswith('Bearer '):
            id_token = request.headers['Authorization'].split('Bearer ')[1]
        
        if not id_token:
            return jsonify({"error": "請提供 Token"}), 401

        try:
            decoded_token = auth.verify_id_token(id_token)
            # 將解碼後的使用者資訊傳遞給路由函式
            return f(decoded_token, *args, **kwargs)
        except firebase_admin.auth.InvalidIdTokenError:
            return jsonify({"error": "無效的 Token"}), 403
        except Exception as e:
            return jsonify({"error": "Token 驗證失敗", "details": str(e)}), 403

    return decorated_function

# 3. 設定資料庫連線資訊
db_config = app.config['DB_CONFIG']

def decimal_to_float(obj):
    if isinstance(obj, list):
        return [decimal_to_float(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, decimal.Decimal):
        return float(obj)
    else:
        return obj

def get_team_results(cursor, team_id, season_id):
    sql = """
    SELECT
        CASE
            WHEN g.winner_team_id = %s THEN 'W'
            WHEN g.winner_team_id IS NULL THEN 'T'
            ELSE 'L'
        END AS result
    FROM games g
    WHERE (g.home_team_id = %s OR g.away_team_id = %s)
      AND g.season_id = %s
      AND g.GameResult = '0'
    ORDER BY g.GameDate DESC, g.game_id DESC
    """
    cursor.execute(sql, (team_id, team_id, team_id, season_id))
    return [row['result'] for row in cursor.fetchall()]

def calc_streak(results):
    if not results:
        return ""
    streak_type = results[0]
    count = 0
    for r in results:
        if r == streak_type:
            count += 1
        else:
            break
    if streak_type == 'W':
        return f'勝{count}'
    elif streak_type == 'L':
        return f'敗{count}'
    else:
        return f'和{count}'
    
def get_batter_stats(cursor, player_id, season_id=None):
    season_filter = ""
    params = [player_id]
    if season_id:
        season_filter = "AND g.season_id = %s"
        params.append(season_id)

    sql = f"""
    SELECT
        COUNT(ge.gameEvent_id) AS plate_appearances, -- 打席
        SUM(rt.is_at_bat) AS at_bats,               -- 打數
        SUM(rt.is_hit) AS hits,                      -- 安打
        SUM(CASE WHEN rt.result_name IN ('二安', '場二') THEN 1 ELSE 0 END) AS doubles,   -- 二安
        SUM(CASE WHEN rt.result_name = '三安' THEN 1 ELSE 0 END) AS triples,   -- 三安
        SUM(CASE WHEN rt.result_name = '全打' THEN 1 ELSE 0 END) AS homeruns,  -- 全壘打
        SUM(CASE WHEN rt.result_name = '一安' THEN 1 ELSE 0 END) AS singles,   -- 一安
        SUM(CASE WHEN rt.result_name = '三振' THEN 1 ELSE 0 END) AS strikeouts, -- 被三振
        SUM(CASE WHEN rt.result_name = '四壞' THEN 1 ELSE 0 END) AS walks,      -- 四壞
        SUM(CASE WHEN rt.result_name = '死球' THEN 1 ELSE 0 END) AS hbp,        -- 死球
        SUM(CASE WHEN rt.result_name IN ('犧飛', '界犧飛') THEN 1 ELSE 0 END) AS sac_fly,    -- 犧飛
        SUM(CASE WHEN rt.result_name = '犧短' THEN 1 ELSE 0 END) AS sac_bunt,   -- 犧短
        SUM(CASE WHEN rt.result_name = '雙殺' THEN 1 ELSE 0 END) AS gidp,       -- 雙殺打
        SUM(CASE WHEN rt.result_name = '故四' THEN 1 ELSE 0 END) AS ibb,        -- 故四
        SUM(CASE WHEN rt.outcome = '出局' AND (rt.result_name LIKE '%滾%' OR rt.result_name LIKE '%地%') THEN 1 ELSE 0 END) AS ground_outs, -- 滾地出局
        SUM(CASE WHEN rt.outcome = '出局' AND rt.result_name LIKE '%飛%' THEN 1 ELSE 0 END) AS fly_outs -- 高飛出局
    FROM gameEvents ge
    JOIN resultType rt ON ge.resultType_id = rt.result_type_id
    JOIN games g ON ge.game_id = g.game_id
    WHERE ge.batter_id = %s {season_filter}
    """
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone() or {}

    # 計算一安（若資料庫沒有一安欄位，則用安打-二安-三安-全壘打）
    singles = (row.get('singles') or ((row.get('hits') or 0) - (row.get('doubles') or 0) - (row.get('triples') or 0) - (row.get('homeruns') or 0)))

    # 壘打數 = 一安*1 + 二安*2 + 三安*3 + 全壘打*4
    total_bases = (singles or 0) + (row.get('doubles') or 0) * 2 + (row.get('triples') or 0) * 3 + (row.get('homeruns') or 0) * 4

    # 打擊率
    avg = (row.get('hits') or 0) / (row.get('at_bats') or 1)
    # 上壘率
    obp = ((row.get('hits') or 0) + (row.get('walks') or 0) + (row.get('hbp') or 0)) / ((row.get('at_bats') or 0) + (row.get('walks') or 0) + (row.get('hbp') or 0) + (row.get('sac_fly') or 0) or 1)
    # 長打率
    slg = total_bases / (row.get('at_bats') or 1)
    # OPS
    ops = obp + slg
    # 滾飛出局比
    gb_fb = (row.get('ground_outs') or 0) / ((row.get('fly_outs') or 1))

    return {
        "打席": row.get('plate_appearances', 0) or 0,
        "打數": row.get('at_bats', 0) or 0,
        "安打": row.get('hits', 0) or 0,
        "一安": singles,
        "二安": row.get('doubles', 0) or 0,
        "三安": row.get('triples', 0) or 0,
        "全壘打": row.get('homeruns', 0) or 0,
        "壘打數": total_bases,
        "被三振": row.get('strikeouts', 0) or 0,
        "四壞": row.get('walks', 0) or 0,
        "死球": row.get('hbp', 0) or 0,
        "犧飛": row.get('sac_fly', 0) or 0,
        "犧短": row.get('sac_bunt', 0) or 0,
        "雙殺打": row.get('gidp', 0) or 0,
        "故四": row.get('ibb', 0) or 0,
        "滾地出局": row.get('ground_outs', 0) or 0,
        "高飛出局": row.get('fly_outs', 0) or 0,
        "打擊率": round(avg, 3),
        "上壘率": round(obp, 3),
        "長打率": round(slg, 3),
        "滾飛出局比": round(gb_fb, 3),
        "整體攻擊指數": round(ops, 3)
    }

def is_batter(player):
    # 判斷不是投手才算打者
    return player.get('position') not in ['投手', 'P', 'Pitcher']

def get_pitcher_stats(cursor, player_id, season_id=None):
    season_filter = ""
    params = [player_id]
    if season_id:
        season_filter = "AND g.season_id = %s"
        params.append(season_id)

    sql = f"""
    SELECT
        COUNT(DISTINCT ge.game_id) AS games_played, -- 出賽數
        COUNT(ge.gameEvent_id) AS batters_faced,    -- 面對打席
        SUM(rt.is_hit) AS hits_allowed,             -- 被安打
        SUM(CASE WHEN rt.result_name = '全打' THEN 1 ELSE 0 END) AS homeruns_allowed, -- 被全壘打
        SUM(CASE WHEN rt.result_name = '四壞' THEN 1 ELSE 0 END) AS walks,            -- 四壞
        SUM(CASE WHEN rt.result_name = '故四' THEN 1 ELSE 0 END) AS ibb,              -- 故四
        SUM(CASE WHEN rt.result_name = '死球' THEN 1 ELSE 0 END) AS hbp,              -- 死球
        SUM(CASE WHEN rt.result_name = '三振' THEN 1 ELSE 0 END) AS strikeouts,       -- 奪三振
        SUM(CASE WHEN rt.outcome = '出局' AND (rt.result_name LIKE '%滾%' OR rt.result_name LIKE '%地%') THEN 1 ELSE 0 END) AS ground_outs, -- 滾地出局
        SUM(CASE WHEN rt.outcome = '出局' AND rt.result_name LIKE '%飛%' THEN 1 ELSE 0 END) AS fly_outs -- 高飛出局
    FROM gameEvents ge
    JOIN resultType rt ON ge.resultType_id = rt.result_type_id
    JOIN games g ON ge.game_id = g.game_id
    LEFT JOIN pitchingDetails pd ON ge.gameEvent_id = pd.gameEvent_id
    WHERE ge.pitcher_id = %s {season_filter}
    """
    cursor.execute(sql, tuple(params))
    row = cursor.fetchone() or {}

    # 滾飛出局比
    gb_fb = (row.get('ground_outs') or 0) / ((row.get('fly_outs') or 1))

    return {
        "出賽數": row.get('games_played', 0) or 0,
        "面對打席": row.get('batters_faced', 0) or 0,
        "被安打": row.get('hits_allowed', 0) or 0,
        "被全壘打": row.get('homeruns_allowed', 0) or 0,
        "四壞": row.get('walks', 0) or 0,
        "故四": row.get('ibb', 0) or 0,
        "死球": row.get('hbp', 0) or 0,
        "奪三振": row.get('strikeouts', 0) or 0,
        "滾地出局": row.get('ground_outs', 0) or 0,
        "高飛出局": row.get('fly_outs', 0) or 0,
        "滾飛出局比": round(gb_fb, 3)
    }

def is_pitcher(player, player_id=None, cursor=None, season_id=None):
    # 判斷 position 或有無投手紀錄
    if player.get('position') in ['投手', 'P', 'Pitcher']:
        return True
    # 若有傳入 player_id 與 cursor，檢查是否有投手紀錄
    if player_id and cursor and season_id:
        sql = """
        SELECT 1 FROM gameEvents ge
        JOIN games g ON ge.game_id = g.game_id
        WHERE ge.pitcher_id = %s AND g.season_id = %s LIMIT 1
        """
        cursor.execute(sql, (player_id, season_id))
        return cursor.fetchone() is not None
    return False

# 4. 建立一個測試路由 (Route)
@app.route('/')
def index():
    print("GET /")
    return "哈囉！這裡是棒球資料系統的後端 API！"

# 5. 建立第一個真正的 API：取得所有球隊列表
@app.route('/teams', methods=['GET'])
def get_teams():
    try:
        # 連接資料庫
        conn = mysql.connector.connect(**db_config)
        # 建立一個可以回傳「字典」格式的 cursor
        cursor = conn.cursor(dictionary=True)

        # 查詢球隊資訊並 join 球場名稱
        query = """
            SELECT t.team_id, t.team_name, t.icon_url, t.gm, t.manager, s.name AS stadium
            FROM teams t
            LEFT JOIN stadiums s ON t.stadium_id = s.stadium_id
        """
        cursor.execute(query)

        # 取得所有查詢結果
        teams = cursor.fetchall()

        # 回傳 JSON 格式的資料
        return jsonify(teams)

    except Exception as e:
        # 如果發生錯誤，回傳錯誤訊息
        return jsonify({"error": str(e)}), 500
    finally:
        # 不論成功或失敗，最後都要關閉 cursor 和 connection
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# 建立第二個 API：根據 ID 取得特定球隊資訊
@app.route('/teams/<int:team_id>', methods=['GET'])
def get_team_by_id(team_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 使用 join 查詢球場名稱
        query = """
            SELECT t.team_id, t.team_name, t.icon_url, t.gm, t.manager, s.name AS stadium
            FROM teams t
            LEFT JOIN stadiums s ON t.stadium_id = s.stadium_id
            WHERE t.team_id = %s
        """
        cursor.execute(query, (team_id,))

        team = cursor.fetchone() # 只取一筆資料

        if team:
            return jsonify(team)
        else:
            return jsonify({"error": "Team not found"}), 404 # 回傳 404 Not Found

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# 建立新的 API：根據 ID 取得球隊資訊以及所有隸屬球員
@app.route('/teams/<int:team_id>/players', methods=['GET'])
def get_team_with_players(team_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 1. 先確認此球隊存在並取得球場名稱
        team_query = """
            SELECT t.team_id, t.team_name, t.icon_url, t.gm, t.manager, s.name AS stadium
            FROM teams t
            LEFT JOIN stadiums s ON t.stadium_id = s.stadium_id
            WHERE t.team_id = %s
        """
        cursor.execute(team_query, (team_id,))
        team = cursor.fetchone()

        if not team:
            return jsonify({"error": "Team not found"}), 404
        
        # 2. 取得目前球季 (這裡假設使用最新的球季)
        season_query = "SELECT * FROM seasons ORDER BY season_id DESC LIMIT 1"
        cursor.execute(season_query)
        current_season = cursor.fetchone()
        
        if not current_season:
            return jsonify({"error": "No seasons found"}), 404
      
        # 3. 找出該球隊所有球員 (透過 contracts 表)    
        player_query = """
        SELECT p.player_id, p.name, p.number, p.position, p.batting_side, p.pitching_side, p.image_url 
        FROM players p
        JOIN contracts c ON p.player_id = c.player_id
        WHERE c.team_id = %s AND c.season_id = %s
        ORDER BY p.name
        """
        cursor.execute(player_query, (team_id, current_season['season_id']))
        players = cursor.fetchall()

        for player in players:
            if is_batter(player):
                stats = get_batter_stats(cursor, player['player_id'], current_season['season_id'])
                player['stats'] = stats
            if is_pitcher(player, player['player_id'], cursor, current_season['season_id']):
                pitcher_stats = get_pitcher_stats(cursor, player['player_id'], current_season['season_id'])
                player['pitcher_stats'] = pitcher_stats
        
        # 4. 組合結果
        result = {
            "teamInfo": {
                "team_id": team["team_id"],
                "team_name": team["team_name"],
                "icon_url": team["icon_url"],
                "gm": team["gm"],
                "manager": team["manager"],
                "stadium": team["stadium"],
                "season": current_season["year"]
            },
            "players": players
        }
        
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# 建立新的 API：列出所有球員資料
@app.route('/players', methods=['GET'])
def get_all_players():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        query = "SELECT * FROM players"
        cursor.execute(query)
        players = cursor.fetchall()

        # 取得最新球季
        cursor.execute("SELECT season_id FROM seasons ORDER BY season_id DESC LIMIT 1")
        season_row = cursor.fetchone()
        season_id = season_row['season_id'] if season_row else None

        for player in players:
            # 打者數據
            if is_batter(player):
                stats = get_batter_stats(cursor, player['player_id'], season_id)
                player['stats'] = stats
            # 投手數據
            if is_pitcher(player, player['player_id'], cursor, season_id):
                pitcher_stats = get_pitcher_stats(cursor, player['player_id'], season_id)
                player['pitcher_stats'] = pitcher_stats

        return jsonify(players)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# 建立新的 API：根據球員ID取得球員資料
@app.route('/players/<int:player_id>', methods=['GET'])
def get_player_by_id(player_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        query = "SELECT * FROM players WHERE player_id = %s"
        cursor.execute(query, (player_id,))
        player = cursor.fetchone()

        if player:
            cursor.execute("SELECT season_id FROM seasons ORDER BY season_id DESC LIMIT 1")
            season_row = cursor.fetchone()
            season_id = season_row['season_id'] if season_row else None

            if is_batter(player):
                stats = get_batter_stats(cursor, player_id, season_id)
                player['stats'] = stats
            if is_pitcher(player, player_id, cursor, season_id):
                pitcher_stats = get_pitcher_stats(cursor, player_id, season_id)
                player['pitcher_stats'] = pitcher_stats
            return jsonify(player)
        else:
            return jsonify({"error": "Player not found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# 建立新的 API：根據比賽日期取得當天所有比賽資料
@app.route('/games/date/<game_date>', methods=['GET'])
def get_games_by_date(game_date):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 查詢當天所有比賽資料
        query = """
            SELECT g.*, 
                   ht.team_name AS home_team_name, 
                   at.team_name AS away_team_name,
                   s.name AS stadium_name
            FROM games g
            LEFT JOIN teams ht ON g.home_team_id = ht.team_id
            LEFT JOIN teams at ON g.away_team_id = at.team_id
            LEFT JOIN stadiums s ON g.stadium_id = s.stadium_id
            WHERE g.GameDate = %s
            ORDER BY g.game_id
        """
        cursor.execute(query, (game_date,))
        games = cursor.fetchall()

        return jsonify(games)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# 建立新的 API：戰績表
@app.route('/standings', methods=['GET'])
def get_standings():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 從資料庫取得最新 season_id
        cursor.execute("SELECT season_id FROM seasons ORDER BY season_id DESC LIMIT 1")
        season_row = cursor.fetchone()
        if not season_row or not season_row['season_id']:
            return jsonify({"error": "No season found"}), 404
        season_id = season_row['season_id']

        sql = """
        SELECT
            t.team_id,
            t.team_name,
            t.icon_url,
            COUNT(g.game_id) AS games_played,
            SUM(CASE WHEN g.winner_team_id = t.team_id THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN g.winner_team_id IS NOT NULL AND g.winner_team_id <> t.team_id AND (g.home_team_id = t.team_id OR g.away_team_id = t.team_id) THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN g.winner_team_id IS NULL AND (g.home_team_id = t.team_id OR g.away_team_id = t.team_id) THEN 1 ELSE 0 END) AS ties,
            ROUND(SUM(CASE WHEN g.winner_team_id = t.team_id THEN 1 ELSE 0 END) / NULLIF(COUNT(g.game_id), 0), 3) AS win_pct
        FROM teams t
        LEFT JOIN games g
            ON (t.team_id = g.home_team_id OR t.team_id = g.away_team_id)
            AND g.GameResult = '0'
        WHERE g.season_id = %s
        GROUP BY t.team_id, t.team_name, t.icon_url
        ORDER BY win_pct DESC, wins DESC
        """
        cursor.execute(sql, (season_id,))
        standings = cursor.fetchall()

        # 加入 streak 欄位
        for team in standings:
            results = get_team_results(cursor, team['team_id'], season_id)
            team['streak'] = calc_streak(results)

        # 計算勝差（以第一名為基準）
        if standings:
            leader = standings[0]
            leader_wins = leader['wins']
            leader_losses = leader['losses']
            for team in standings:
                gb = ((leader_wins - team['wins']) + (team['losses'] - leader_losses)) / 2
                if gb == 0:
                    team['games_behind'] = '-'
                else:
                    team['games_behind'] = round(gb, 1)

        standings = decimal_to_float(standings)
        return jsonify(standings)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

# 建立新的 API：根據球員名稱模糊搜尋球員
@app.route('/players/search', methods=['GET'])
def search_players_by_name():
    try:
        # 從前端獲取查詢參數
        player_name = request.args.get('name', default='', type=str)

        # 如果名稱為空，回傳錯誤
        if not player_name:
            return jsonify({"error": "Player name is required"}), 400

        # 連接資料庫
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 執行模糊搜尋的 SQL 查詢
        query = """
        SELECT player_id, name
        FROM players
        WHERE name LIKE %s
        """
        cursor.execute(query, (f"%{player_name}%",))
        players = cursor.fetchall()

        # 回傳符合的球員 ID 和名稱
        return jsonify(players)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

@app.route('/games/<game_id>/boxscore', methods=['GET'])
def get_game_boxscore(game_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 1. 獲取比賽基本資料
        game_query = """
            SELECT g.*, 
                   ht.team_name AS home_team_name, 
                   ht.icon_url AS home_team_icon,
                   at.team_name AS away_team_name,
                   at.icon_url AS away_team_icon,
                   s.name AS stadium_name
            FROM games g
            JOIN teams ht ON g.home_team_id = ht.team_id
            JOIN teams at ON g.away_team_id = at.team_id
            LEFT JOIN stadiums s ON g.stadium_id = s.stadium_id
            WHERE g.game_id = %s
        """
        cursor.execute(game_query, (game_id,))
        game = cursor.fetchone()

        if not game:
            return jsonify({"error": "Game not found"}), 404
        
        # 2. 獲取每局得分情況 - 包含所有局數
        inning_query = """
        SELECT
            ge.InningSeq,
            MAX(ge.HomeScore) AS home_score_end,
            MAX(ge.VisitingScore) AS away_score_end
        FROM gameEvents ge
        WHERE ge.game_id = %s
        GROUP BY ge.InningSeq
        ORDER BY CAST(ge.InningSeq AS UNSIGNED)
        """
        cursor.execute(inning_query, (game_id,))
        innings = cursor.fetchall()
        
        # 找出最大局數，處理延長賽
        max_inning = 9  # 預設至少9局
        if innings:
            max_inning = max(max_inning, max([int(inning['InningSeq']) for inning in innings]))
        
        # 3. 處理資料，計算每局得分
        home_innings = [0] * max_inning  # 動態調整陣列大小
        away_innings = [0] * max_inning
        
        prev_home_score = 0
        prev_away_score = 0
        
        for inning in innings:
            idx = int(inning['InningSeq']) - 1
            current_home_score = int(inning['home_score_end'])
            current_away_score = int(inning['away_score_end'])

            print(f"Inning {idx + 1}: Home {current_home_score}, Away {current_away_score}")
            
            # 計算本局得分 = 當前總分 - 上一局總分
            home_innings[idx] = current_home_score - prev_home_score
            away_innings[idx] = current_away_score - prev_away_score
            
            prev_home_score = current_home_score
            prev_away_score = current_away_score
        
        # 4. 獲取安打和失誤數據 - 保持原來的查詢邏輯
        stats_query = """
        SELECT
            SUM(CASE 
                WHEN ge.batter_id IN (
                    SELECT c.player_id 
                    FROM contracts c 
                    WHERE c.team_id = g.home_team_id AND c.season_id = g.season_id
                ) AND rt.is_hit = TRUE THEN 1 
                ELSE 0 
            END) AS home_hits,
            
            SUM(CASE 
                WHEN ge.batter_id IN (
                    SELECT c.player_id 
                    FROM contracts c 
                    WHERE c.team_id = g.away_team_id AND c.season_id = g.season_id
                ) AND rt.is_hit = TRUE THEN 1 
                ELSE 0 
            END) AS away_hits,
            
            SUM(CASE 
                WHEN ge.batter_id IN (
                    SELECT c.player_id 
                    FROM contracts c 
                    WHERE c.team_id = g.away_team_id AND c.season_id = g.season_id
                ) AND rt.result_name LIKE '%失%' THEN 1 
                ELSE 0 
            END) AS home_errors,
            
            SUM(CASE 
                WHEN ge.batter_id IN (
                    SELECT c.player_id 
                    FROM contracts c 
                    WHERE c.team_id = g.home_team_id AND c.season_id = g.season_id
                ) AND rt.result_name LIKE '%失%' THEN 1 
                ELSE 0 
            END) AS away_errors
        FROM gameEvents ge
        JOIN games g ON ge.game_id = g.game_id
        JOIN resultType rt ON ge.resultType_id = rt.result_type_id
        WHERE ge.game_id = %s
        """
        cursor.execute(stats_query, (game_id,))
        stats = cursor.fetchone() or {"home_hits": 0, "away_hits": 0, "home_errors": 0, "away_errors": 0}
        
        # 5. 組合最終結果
        boxscore = {
            "game_info": {
                "game_id": game["game_id"],
                "date": game["GameDate"],
                "stadium": game["stadium_name"],
                "status": "完賽" if game["GameResult"] == "0" else "進行中",
                "total_innings": max_inning,  # 新增: 總局數
                "is_extra": max_inning > 9    # 新增: 是否有延長賽
            },
            "home_team": {
                "team_id": game["home_team_id"],
                "team_name": game["home_team_name"],
                "icon_url": game["home_team_icon"],
                "innings": home_innings,
                "runs": int(game["home_team_score"]),
                "hits": stats["home_hits"] or 0,
                "errors": stats["home_errors"] or 0
            },
            "away_team": {
                "team_id": game["away_team_id"],
                "team_name": game["away_team_name"],
                "icon_url": game["away_team_icon"],
                "innings": away_innings,
                "runs": int(game["away_team_score"]),
                "hits": stats["away_hits"] or 0,
                "errors": stats["away_errors"] or 0
            }
        }
        
        return jsonify(boxscore)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

@app.route('/api/users/sync', methods=['POST'])
@check_auth
def sync_user(decoded_token):
    try:
        # 獲取用戶資料
        user_data = request.json
        uid = decoded_token['uid']

        # 連接資料庫
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 檢查用戶是否已存在
        cursor.execute("SELECT * FROM users WHERE uid = %s", (uid,))
        existing_user = cursor.fetchone()

        if existing_user:
            # 更新現有用戶
            cursor.execute("""
                UPDATE users 
                SET email = %s, display_name = %s, photo_url = %s, updated_at = CURRENT_TIMESTAMP
                WHERE uid = %s
            """, (user_data.get('email'), user_data.get('displayName'), user_data.get('photoURL'), uid))
        else:
            # 創建新用戶
            cursor.execute("""
                INSERT INTO users (uid, email, display_name, photo_url)
                VALUES (%s, %s, %s, %s)
            """, (uid, user_data.get('email'), user_data.get('displayName'), user_data.get('photoURL')))

        conn.commit()

        return jsonify({
            'message': '用戶資料同步成功',
            'user': {
                'uid': uid,
                'email': user_data.get('email'),
                'displayName': user_data.get('displayName'),
                'photoURL': user_data.get('photoURL')
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

@app.route('/api/users/me', methods=['GET'])
@check_auth
def get_user(decoded_token):
    try:
        uid = decoded_token['uid']
        
        # 連接資料庫
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 從資料庫獲取用戶資料
        cursor.execute("SELECT * FROM users WHERE uid = %s", (uid,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"error": "用戶不存在"}), 404

        return jsonify({
            'uid': user['uid'],
            'email': user['email'],
            'displayName': user['display_name'],
            'photoURL': user['photo_url'],
            'role': user.get('role', 'user')
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

@app.route('/api/preferences/follow', methods=['POST'])
@check_auth
def follow_player(decoded_token):
    try:
        uid = decoded_token['uid']
        player_id = request.json.get('player_id')
        
        if not player_id:
            return jsonify({"error": "缺少球員ID"}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 使用 INSERT ... ON DUPLICATE KEY UPDATE
        cursor.execute("""
            INSERT INTO user_preferences (uid, player_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE preference = 'follow'
        """, (uid, player_id))
        
        conn.commit()
        return jsonify({"message": "追蹤成功"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

@app.route('/api/preferences/unfollow', methods=['POST'])
@check_auth
def unfollow_player(decoded_token):
    try:
        uid = decoded_token['uid']
        player_id = request.json.get('player_id')
        
        if not player_id:
            return jsonify({"error": "缺少球員ID"}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            DELETE FROM user_preferences
            WHERE uid = %s AND player_id = %s
        """, (uid, player_id))
        
        conn.commit()
        return jsonify({"message": "取消追蹤成功"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

@app.route('/api/preferences/note', methods=['POST'])
@check_auth
def update_note(decoded_token):
    try:
        uid = decoded_token['uid']
        player_id = request.json.get('player_id')
        note = request.json.get('note')
        
        if not player_id:
            return jsonify({"error": "缺少球員ID"}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 檢查是否已有追蹤記錄
        cursor.execute("""
            SELECT 1 FROM user_preferences 
            WHERE uid = %s AND player_id = %s
        """, (uid, player_id))
        
        if not cursor.fetchone():
            return jsonify({"error": "尚未追蹤此球員"}), 400

        # 更新備註
        cursor.execute("""
            UPDATE user_preferences
            SET note = %s
            WHERE uid = %s AND player_id = %s
        """, (note, uid, player_id))
        
        conn.commit()
        return jsonify({"message": "備註更新成功"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor is not None:
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()

@app.route('/api/preferences/following', methods=['GET'])
@check_auth
def get_following_players(decoded_token):
    conn = None
    cursor = None
    try:
        uid = decoded_token['uid']
        print(f"Fetching following players for user: {uid}")
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # 檢查 user_preferences 表是否存在
        cursor.execute("SHOW TABLES LIKE 'user_preferences'")
        if not cursor.fetchone():
            print("user_preferences table does not exist")
            return jsonify({"error": "資料表不存在"}), 500

        # 檢查使用者是否存在於 user_preferences 表中
        cursor.execute("SELECT COUNT(*) as count FROM user_preferences WHERE uid = %s", (uid,))
        result = cursor.fetchone()
        if result['count'] == 0:
            print(f"No preferences found for user: {uid}")
            return jsonify([])

        # 修改查詢以包含所有必要的球員資訊
        query = """
            SELECT 
                p.player_id,
                p.name,
                p.number,
                p.position,
                p.batting_side,
                p.pitching_side,
                p.image_url,
                COALESCE(up.note, '') as note
            FROM players p
            JOIN user_preferences up ON p.player_id = up.player_id
            WHERE up.uid = %s AND up.preference = 'follow'
            ORDER BY p.name
        """
        print(f"Executing query: {query}")
        cursor.execute(query, (uid,))
        
        players = cursor.fetchall()
        print(f"Found {len(players)} players")
        
        # 確保所有必要的欄位都存在
        for player in players:
            player['is_following'] = True
            # 確保所有欄位都有預設值
            player.setdefault('batting_side', '')
            player.setdefault('pitching_side', '')
            player.setdefault('image_url', '')
            player.setdefault('note', '')
            player.setdefault('number', '')
            player.setdefault('position', '')

        return jsonify(players)

    except mysql.connector.Error as e:
        print(f"Database error: {str(e)}")
        print(f"Error code: {e.errno}")
        print(f"SQL state: {e.sqlstate}")
        return jsonify({"error": f"資料庫錯誤: {str(e)}"}), 500
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        print(f"Error type: {type(e)}")
        return jsonify({"error": f"伺服器錯誤: {str(e)}"}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()

# 6. 讓程式可以被執行
if __name__ == '__main__':
    app.run(debug=True, port=int(os.getenv("APP_PORT", default=5000)), host='0.0.0.0')