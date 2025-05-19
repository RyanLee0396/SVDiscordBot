import aiosqlite
from contextlib import asynccontextmanager
import asyncio
from typing import Optional, List, Tuple, Any

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._locks = {}

    @asynccontextmanager
    async def get_connection(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def execute(self, query: str, params: Optional[tuple] = None) -> Any:
        async with self.get_connection() as db:
            async with db.cursor() as cursor:
                await cursor.execute(query, params or ())
                await db.commit()
                return cursor

    async def fetch_one(self, query: str, params: Optional[tuple] = None) -> Optional[dict]:
        async with self.get_connection() as db:
            async with db.cursor() as cursor:
                await cursor.execute(query, params or ())
                result = await cursor.fetchone()
                return dict(result) if result else None

    async def fetch_all(self, query: str, params: Optional[tuple] = None) -> List[dict]:
        async with self.get_connection() as db:
            async with db.cursor() as cursor:
                await cursor.execute(query, params or ())
                results = await cursor.fetchall()
                return [dict(row) for row in results]

    async def acquire_lock(self, lock_id: str) -> asyncio.Lock:
        if lock_id not in self._locks:
            self._locks[lock_id] = asyncio.Lock()
        return await self._locks[lock_id].acquire()

    def release_lock(self, lock_id: str):
        if lock_id in self._locks:
            self._locks[lock_id].release()

    async def transaction(self):
        async with self.get_connection() as db:
            async with db.cursor() as cursor:
                await cursor.execute("BEGIN TRANSACTION")
                try:
                    yield cursor
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    raise e

# Initialize database tables
async def init_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        async with db.cursor() as cursor:
            # Create scrims table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS scrims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time_period TEXT UNIQUE
                )
            """)
            
            # Create teams table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_name TEXT UNIQUE,
                    scrim_time TEXT,
                    leader_id INTEGER,
                    FOREIGN KEY (leader_id) REFERENCES members(id)
                )
            """)
            
            # Create members table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER,
                    member_name TEXT,
                    FOREIGN KEY (team_id) REFERENCES teams(id)
                )
            """)
            
            # Add indexes
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_scrims_time_period ON scrims(time_period)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_team_name ON teams(team_name)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_scrim_time ON teams(scrim_time)")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_members_member_name ON members(member_name)")
            
            await db.commit() 