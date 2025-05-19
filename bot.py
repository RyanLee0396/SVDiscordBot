import discord
from discord.ext import commands
from discord.ui import Select, View, Button
import sqlite3
import pytz
from datetime import datetime, timedelta
import os
from database import DatabaseManager, init_db
from tenacity import retry, stop_after_attempt, wait_exponential

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DB_PATH = "scrim_bot.db"

# Initialize database
db = DatabaseManager(DB_PATH)

# --- Database Setup ---
conn = sqlite3.connect("scrim_bot.db")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS scrims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time_period TEXT UNIQUE
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name TEXT,
    scrim_time TEXT,
    leader_id INTEGER,
    FOREIGN KEY (leader_id) REFERENCES members(id)
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER,
    member_name TEXT,
    FOREIGN KEY (team_id) REFERENCES teams(id)
)
""")
# Add indexes for optimization
cursor.execute("CREATE INDEX IF NOT EXISTS idx_scrims_time_period ON scrims(time_period)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_team_name ON teams(team_name)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_scrim_time ON teams(scrim_time)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_members_member_name ON members(member_name)")
conn.commit()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.messages = True
intents.members = True  # Enable the members intent
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents, application_id="1372785675107045447")

# --- Helper Functions ---
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def safe_discord_operation(operation, *args, **kwargs):
    """Safely execute Discord operations with retry logic"""
    try:
        return await operation(*args, **kwargs)
    except discord.HTTPException as e:
        if e.status == 429:  # Rate limit
            raise  # Let retry handle it
        raise

async def get_scrim_times():
    return await db.fetch_all("SELECT time_period FROM scrims")

async def add_scrim_time(time_period):
    try:
        async with db.transaction() as cursor:
            await cursor.execute("INSERT INTO scrims (time_period) VALUES (?)", (time_period,))
        return True
    except Exception:
        return False

async def add_team(team_name, scrim_time, leader_id):
    async with db.transaction() as cursor:
        await cursor.execute(
            "INSERT INTO teams (team_name, scrim_time, leader_id) VALUES (?, ?, ?)",
            (team_name, scrim_time, leader_id)
        )
        return cursor.lastrowid

async def get_teams(scrim_time):
    results = await db.fetch_all("SELECT team_name FROM teams WHERE scrim_time = ?", (scrim_time,))
    return [row['team_name'] for row in results]

async def add_team_member(team_id, member_name):
    async with db.transaction() as cursor:
        # Check if team exists and has space
        await cursor.execute("SELECT COUNT(*) as count FROM members WHERE team_id = ?", (team_id,))
        result = await cursor.fetchone()
        if result['count'] < 5:  # Assuming max 5 members per team
            await cursor.execute(
                "INSERT INTO members (team_id, member_name) VALUES (?, ?)",
                (team_id, member_name)
            )
            return True
        return False

async def get_team_members(team_id):
    results = await db.fetch_all("SELECT member_name FROM members WHERE team_id = ?", (team_id,))
    return [row['member_name'] for row in results]

async def get_team_id(team_name):
    result = await db.fetch_one("SELECT id FROM teams WHERE team_name = ?", (team_name,))
    return result['id'] if result else None

# Define the get_current_week_period function

def get_current_week_period():
    """Calculate the start and end dates of the current week."""
    korean_tz = pytz.timezone('Asia/Seoul')
    today = datetime.now(korean_tz)
    start_date = today - timedelta(days=today.weekday())  # Start of the week (Monday)
    end_date = start_date + timedelta(days=6)  # End of the week (Sunday)
    return start_date.strftime('%d %B'), end_date.strftime('%d %B')

def create_info_embed(title, description, color=discord.Color.blue()):
    """Create a standard info embed with consistent styling"""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now()
    )
    embed.set_footer(text="SV Bot | Scrim Management")
    return embed

def create_team_list_embed(teams_data):
    """Create an embed for displaying team lists"""
    embed = discord.Embed(
        title="📋 Team List",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    for team in teams_data:
        embed.add_field(name=f"Team {team['name']}", value=f"Members: {team['members']}", inline=False)
    embed.set_footer(text="SV Bot | Team Management")
    return embed

def create_schedule_embed(team_name, scrim_times):
    """Create an embed for displaying scrim schedules"""
    embed = discord.Embed(
        title=f"📅 Scrim Schedule - {team_name}",
        color=discord.Color.green(),
        timestamp=datetime.now()
    )
    if scrim_times:
        for time in scrim_times:
            embed.add_field(name="Scheduled Date", value=time, inline=False)
    else:
        embed.description = "No scheduled scrims"
    embed.set_footer(text="SV Bot | Schedule Management")
    return embed

# --- Sync Commands ---
@bot.event
async def on_ready():
    await init_db(DB_PATH)
    await bot.tree.sync()
    print(f"Bot is logged in as {bot.user}")

# --- Menu View ---
class MenuView(View):
    def __init__(self):
        super().__init__(timeout=None)  # Menu doesn't timeout

        # First layer: Category Buttons
        self.add_item(Button(label="Team", style=discord.ButtonStyle.primary, custom_id="category_team"))
        self.add_item(Button(label="Scrim", style=discord.ButtonStyle.primary, custom_id="category_scrim"))
        self.add_item(Button(label="All Teams", style=discord.ButtonStyle.secondary, custom_id="check_teams"))
        self.add_item(Button(label="Participants", style=discord.ButtonStyle.primary, custom_id="participants"))

        # Reset Database Button
        self.add_item(Button(label="Reset Database", style=discord.ButtonStyle.danger, custom_id="reset_database"))

# Add a new class for the second layer of buttons
class TeamView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="Create Team", style=discord.ButtonStyle.success, custom_id="create_team"))
        self.add_item(Button(label="Join Team", style=discord.ButtonStyle.success, custom_id="join_team"))
        self.add_item(Button(label="Quit Team", style=discord.ButtonStyle.secondary, custom_id="quit_team"))
        self.add_item(Button(label="Discard Team", style=discord.ButtonStyle.danger, custom_id="discard_team"))
        self.add_item(Button(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="back_to_menu"))

class ScrimView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="Scrim Signup", style=discord.ButtonStyle.success, custom_id="scrim_signup"))
        self.add_item(Button(label="Cancel Sign Up", style=discord.ButtonStyle.danger, custom_id="cancel_sign_up"))
        self.add_item(Button(label="Check Schedule", style=discord.ButtonStyle.secondary, custom_id="check_schedule"))
        self.add_item(Button(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="back_to_menu"))

# --- Interaction Handlers ---
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id")

        if custom_id == "list_teams":
            scrim_time = "21:00"  # Replace with the desired scrim time
            await list_teams_logic(interaction, scrim_time)
        elif custom_id == "reset_database":
            await reset_database_logic(interaction)
        elif custom_id == "create_team":
            await create_team_logic(interaction)
        elif custom_id == "join_team":
            await join_team_logic(interaction)
        elif custom_id == "check_teams":
            await check_teams_logic(interaction)
        elif custom_id == "quit_team":
            await quit_team_logic(interaction)
        elif custom_id == "scrim_signup":
            await scrim_signup_logic(interaction)
        elif custom_id == "cancel_sign_up":
            await cancel_sign_up_logic(interaction)
        elif custom_id == "check_schedule":
            await check_schedule_logic(interaction)
        elif custom_id == "discard_team":
            await discard_team_logic(interaction)
        elif custom_id == "participants":
            await participants_logic(interaction)
        elif custom_id.startswith("participants_prev_") or custom_id.startswith("participants_next_"):
            day_offset = int(custom_id.split("_")[-1])
            await participants_logic(interaction, day_offset)
        elif custom_id == "category_team":
            view = TeamView()
            await interaction.response.edit_message(content="Team Management", embed=None, view=view)
        elif custom_id == "category_scrim":
            view = ScrimView()
            await interaction.response.edit_message(content="Scrim Management", embed=None, view=view)
        elif custom_id == "back_to_menu":
            embed = discord.Embed(
                title="🎮 SV Bot Menu",
                description="Welcome to SV Bot! Select an option below:",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Team Management", value="• List Teams\n• Create Team\n• Join Team\n• Check Teams\n• Quit Team\n• Discard Team", inline=True)
            embed.add_field(name="Scrim Management", value="• Scrim Signup\n• Cancel Sign Up\n• Check Schedule", inline=True)
            embed.add_field(name="Admin", value="• Reset Database", inline=True)
            embed.set_footer(text="SV Bot | Main Menu")
            view = MenuView()
            await interaction.response.edit_message(embed=embed, view=view)

# --- Commands ---
@bot.tree.command(name="menu")
async def menu(interaction: discord.Interaction):
    """Command to display the interaction menu"""
    embed = discord.Embed(
        title="🎮 SV Bot Menu",
        description="Welcome to SV Bot! Select an option below:",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    
    embed.add_field(name="Team Management", value="• List Teams\n• Create Team\n• Join Team\n• Check Teams\n• Quit Team\n• Discard Team", inline=True)
    embed.add_field(name="Scrim Management", value="• Scrim Signup\n• Cancel Sign Up\n• Check Schedule", inline=True)
    embed.add_field(name="Admin", value="• Reset Database", inline=True)
    
    embed.set_footer(text="SV Bot | Main Menu")
    view = MenuView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# --- Logic Functions ---
async def list_teams_logic(interaction: discord.Interaction, scrim_time: str):
    """Logic to list all teams registered for a specific scrim time"""
    teams = get_teams(scrim_time)
    if teams:
        teams_data = []
        for team_name in teams:
            team_id = get_team_id(team_name)
            members = get_team_members(team_id)
            teams_data.append({"name": team_name, "members": ", ".join(members) if members else "No members"})
        embed = create_team_list_embed(teams_data)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = create_info_embed("No Teams", f"No teams registered for {scrim_time}.", discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def reset_database_logic(interaction: discord.Interaction):
    """Logic to reset the database to empty"""
    try:
        cursor.execute("DELETE FROM scrims")
        cursor.execute("DELETE FROM teams")
        cursor.execute("DELETE FROM members")
        conn.commit()
        embed = create_info_embed("Database Reset", "Database has been reset successfully!", discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        embed = create_info_embed("Error", f"Failed to reset database: {e}", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def create_team_logic(interaction: discord.Interaction):
    """Logic to create a new team and assign the Team leader role"""
    leader_id = interaction.user.id
    cursor.execute("SELECT team_name FROM teams WHERE leader_id = ?", (leader_id,))
    if cursor.fetchone() is not None:
        embed = create_info_embed("Error", "You are already a Team leader and cannot create another team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = create_info_embed("Create Team", "Please enter your team name (3 characters max):", discord.Color.blue())
    await interaction.response.send_message(embed=embed, ephemeral=True)

    def check(msg):
        return msg.author == interaction.user and len(msg.content) <= 3

    try:
        team_msg = await bot.wait_for("message", check=check, timeout=60)
        team_name = team_msg.content.upper()
        if get_team_id(team_name) is not None:
            embed = create_info_embed("Error", "This team name is already taken. Please choose a different name.", discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        add_team(team_name, "", leader_id)
        role = discord.utils.get(interaction.guild.roles, name="Team leader")
        await interaction.user.add_roles(role)
        await interaction.user.edit(nick=f"{team_name} {interaction.user.display_name}(C)")
        
        embed = create_info_embed("Success", f"Team '{team_name}' created and role 'Team leader' assigned.", discord.Color.green())
        view = View()
        view.add_item(Button(label="Sign Up for Scrim", style=discord.ButtonStyle.success, custom_id="scrim_signup"))
        view.add_item(Button(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="back_to_menu"))
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        embed = create_info_embed("Error", "Failed to create team. Please try again.", discord.Color.red())
        await interaction.followup.send(embed=embed)

async def join_team_logic(interaction: discord.Interaction):
    """Logic to join a team by selecting from a list and assign a role"""
    leader_id = interaction.user.id
    cursor.execute("SELECT team_name FROM teams WHERE leader_id = ?", (leader_id,))
    if cursor.fetchone() is not None:
        embed = create_info_embed("Error", "Team leaders cannot join another team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    cursor.execute("SELECT team_id FROM members WHERE member_name = ?", (interaction.user.display_name,))
    if cursor.fetchone() is not None:
        embed = create_info_embed("Error", "You are already a member of a team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    cursor.execute("SELECT team_name FROM teams")
    team_names = [row[0] for row in cursor.fetchall()]
    if not team_names:
        embed = create_info_embed("No Teams Available", "There are currently no teams available to join.", discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    options = [discord.SelectOption(label=team) for team in team_names]
    select = Select(placeholder="Select a team to join", options=options)

    async def select_callback(interaction):
        selected_team = select.values[0]
        role = discord.utils.get(interaction.guild.roles, name=selected_team)
        if not role:
            role = await interaction.guild.create_role(name=selected_team)
        await interaction.user.add_roles(role)
        await interaction.user.edit(nick=f"{selected_team} {interaction.user.display_name}")
        embed = create_info_embed("Success", f"You have joined the team '{selected_team}'.", discord.Color.green())
        view = View()
        view.add_item(Button(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="back_to_menu"))
        await interaction.response.edit_message(embed=embed, view=view)

    select.callback = select_callback
    view = View()
    view.add_item(select)
    
    embed = create_info_embed("Join Team", "Select a team to join:", discord.Color.blue())
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def check_teams_logic(interaction: discord.Interaction):
    """Logic to check all teams and their members"""
    cursor.execute("SELECT team_name, leader_id FROM teams")
    teams = cursor.fetchall()
    if teams:
        embed = discord.Embed(
            title="📋 Teams and Members",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        number_emojis = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        for index, team in enumerate(teams, start=1):
            team_name, leader_id = team
            member = interaction.guild.get_member(leader_id)
            if member:
                leader_name = member.display_name
                if leader_name.startswith(f"{team_name} "):
                    leader_name = leader_name[len(team_name) + 1:].strip()
                if leader_name.endswith("(C)"):
                    leader_name = leader_name[:-3].strip()
                leader_name += " (C)"
            else:
                leader_name = "(Leader not found)"

            team_id = get_team_id(team_name)
            members = get_team_members(team_id)
            member_names = ", ".join([name for name in members if name != leader_name])
            
            index_str = ''.join(number_emojis[int(digit)] for digit in str(index))
            embed.add_field(
                name=f"{index_str} {team_name}",
                value=f"👥 Members: {leader_name}, {member_names}",
                inline=False
            )
        
        embed.set_footer(text="SV Bot | Team List")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = create_info_embed("No Teams", "No teams found.", discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def quit_team_logic(interaction: discord.Interaction):
    """Logic to quit a team and remove the role"""
    cursor.execute("SELECT team_name FROM teams WHERE leader_id = ?", (interaction.user.id,))
    if cursor.fetchone() is not None:
        embed = create_info_embed("Error", "Team leaders cannot quit their own team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    team_roles = [role for role in interaction.user.roles if role.name in [row[0] for row in cursor.execute("SELECT team_name FROM teams")]]
    if team_roles:
        for role in team_roles:
            await interaction.user.remove_roles(role)
            if interaction.user.nick and interaction.user.nick.startswith(f"{role.name} "):
                original_nick = interaction.user.nick.split(' - ', 1)[-1]
                await interaction.user.edit(nick=original_nick)
        embed = create_info_embed("Success", "You have quit the team.", discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = create_info_embed("Error", "You are not part of any team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def scrim_signup_logic(interaction: discord.Interaction):
    """Logic to sign up for scrims for the week with proper concurrency handling"""
    korean_tz = pytz.timezone('Asia/Seoul')
    start_date = datetime.now(korean_tz)
    days_of_week = [(start_date + timedelta(days=i)).strftime('%d/%m') for i in range(7)]
    
    leader_id = interaction.user.id
    team = await db.fetch_one("SELECT team_name FROM teams WHERE leader_id = ?", (leader_id,))
    
    if not team:
        embed = create_info_embed("Error", "You are not a leader of any team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    team_name = team['team_name']
    signed_up_days = await db.fetch_all(
        "SELECT scrim_time FROM teams WHERE team_name = ?",
        (team_name,)
    )
    signed_up_days = [row['scrim_time'] for row in signed_up_days]

    available_days = []
    for day in days_of_week:
        if day not in signed_up_days:
            # Check team count with proper locking
            async with db.transaction() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) as count FROM teams WHERE scrim_time = ?",
                    (day,)
                )
                result = await cursor.fetchone()
                if result['count'] < 12:
                    available_days.append(day)

    if not available_days:
        embed = create_info_embed(
            "No Available Slots",
            "There are no available scrim slots for your team.",
            discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    options = [discord.SelectOption(label=day) for day in available_days]
    select = Select(
        placeholder="Select scrim days",
        options=options,
        min_values=1,
        max_values=len(available_days)
    )

    async def select_callback(interaction):
        selected_days = select.values
        scrim_time = "9:00 PM KST"
        embed = discord.Embed(
            title="📅 Scrim Signup Results",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )

        for day in selected_days:
            async with db.transaction() as cursor:
                # Double-check availability with proper locking
                await cursor.execute(
                    "SELECT COUNT(*) as count FROM teams WHERE scrim_time = ?",
                    (day,)
                )
                result = await cursor.fetchone()
                if result['count'] < 12:
                    await cursor.execute(
                        "INSERT INTO teams (team_name, scrim_time, leader_id) VALUES (?, ?, ?)",
                        (team_name, day, leader_id)
                    )
                    embed.add_field(
                        name="✅ Success",
                        value=f"Team signed up for scrim on {day} at {scrim_time}",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="❌ Failed",
                        value=f"The scrim on '{day}' is full. Please select another day.",
                        inline=False
                    )

        embed.set_footer(text="SV Bot | Scrim Signup")
        view = View()
        view.add_item(Button(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="back_to_menu"))
        await interaction.response.edit_message(embed=embed, view=view)

    select.callback = select_callback
    view = View()
    view.add_item(select)
    
    embed = create_info_embed("Scrim Signup", "Select scrim days to sign up (multiple):", discord.Color.blue())
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def cancel_sign_up_logic(interaction: discord.Interaction):
    """Logic to cancel specific scrim sign-up dates for a team"""
    leader_id = interaction.user.id
    cursor.execute("SELECT team_name FROM teams WHERE leader_id = ?", (leader_id,))
    team = cursor.fetchone()
    if team:
        team_name = team[0]
        cursor.execute("SELECT scrim_time FROM teams WHERE team_name = ?", (team_name,))
        scrim_times = [row[0] for row in cursor.fetchall() if row[0]]
        if scrim_times:
            options = [discord.SelectOption(label=scrim_time[:100], value=scrim_time[:100]) for scrim_time in scrim_times]
            select = Select(placeholder="Select scrim dates to cancel", options=options, min_values=1, max_values=len(scrim_times))

            async def select_callback(interaction):
                selected_dates = select.values
                for date in selected_dates:
                    cursor.execute("DELETE FROM teams WHERE team_name = ? AND scrim_time = ?", (team_name, date))
                conn.commit()
                embed = create_info_embed("Success", f"Cancelled sign-up for dates: {', '.join(selected_dates)}.", discord.Color.green())
                view = View()
                view.add_item(Button(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="back_to_menu"))
                await interaction.response.edit_message(embed=embed, view=view)

            select.callback = select_callback
            view = View()
            view.add_item(select)
            
            embed = create_info_embed("Cancel Signup", "Select scrim dates to cancel:", discord.Color.blue())
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            embed = create_info_embed("No Scrims", f"Team '{team_name}' is not signed up for any scrim schedules.", discord.Color.orange())
            await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = create_info_embed("Error", "You are not a leader of any team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def check_schedule_logic(interaction: discord.Interaction):
    """Logic to check the scrim schedule for the user's team"""
    user_id = interaction.user.id

    cursor.execute("SELECT team_name FROM teams WHERE leader_id = ?", (user_id,))
    team_data = cursor.fetchone()

    if not team_data:
        cursor.execute("""
            SELECT teams.team_name
            FROM teams
            INNER JOIN members ON teams.id = members.team_id
            WHERE members.member_name = ?
        """, (interaction.user.display_name,))
        team_data = cursor.fetchone()

    if team_data:
        team_name = team_data[0]
        cursor.execute("SELECT scrim_time FROM teams WHERE team_name = ?", (team_name,))
        scrim_times = [row[0] for row in cursor.fetchall() if row[0]]
        
        embed = create_schedule_embed(team_name, scrim_times)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = create_info_embed("Error", "You are not part of any team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def discard_team_logic(interaction: discord.Interaction):
    """Logic to discard a team for the Team leader"""
    leader_id = interaction.user.id
    cursor.execute("SELECT team_name, id FROM teams WHERE leader_id = ?", (leader_id,))
    team = cursor.fetchone()
    if team:
        team_name, team_id = team

        cursor.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        cursor.execute("DELETE FROM members WHERE team_id = ?", (team_id,))
        conn.commit()

        role = discord.utils.get(interaction.guild.roles, name=team_name.upper())
        if role:
            for member in role.members:
                await member.remove_roles(role)
                if member.nick and member.nick.startswith(f"{team_name} "):
                    original_nick = member.nick.split(' ', 1)[-1]
                    await member.edit(nick=original_nick)
            await role.delete()

        team_leader_role = discord.utils.get(interaction.guild.roles, name="Team leader")
        if team_leader_role and team_leader_role in interaction.user.roles:
            await interaction.user.remove_roles(team_leader_role)

        if interaction.user.nick and interaction.user.nick.startswith(f"{team_name} "):
            original_nick = interaction.user.nick.split(' ', 1)[-1]
            if original_nick.endswith("(C)"):
                original_nick = original_nick[:-3]
            await interaction.user.edit(nick=original_nick)

        embed = create_info_embed("Team Discarded", f"Your team '{team_name}' has been discarded.", discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = create_info_embed("Error", "You are not a leader of any team.", discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def participants_logic(interaction: discord.Interaction, day_offset=0):
    """Logic to display all current signed-up teams for a specific scrim day with navigation buttons, limited to a 1-week period"""
    korean_tz = pytz.timezone('Asia/Seoul')
    start_date = datetime.now(korean_tz) + timedelta(days=day_offset)
    scrim_date = start_date.strftime('%d/%m')
    cursor.execute("SELECT team_name FROM teams WHERE scrim_time = ?", (scrim_date,))
    teams = [row[0] for row in cursor.fetchall()]
    embed = discord.Embed(
        title=f"📋 Participants for {scrim_date}",
        description="List of teams signed up for the scrim:",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    if teams:
        number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟", "1️⃣1️⃣", "1️⃣2️⃣"]
        for index, team in enumerate(teams, start=1):
            emoji = number_emojis[index - 1] if index <= len(number_emojis) else "🔢"
            embed.add_field(name=f"{emoji} {team}", value="Signed up", inline=False)
    else:
        embed.add_field(name="No Teams", value="None", inline=False)
    embed.set_footer(text="SV Bot | Daily Participants")

    # Add navigation buttons with boundary checks
    view = View()
    if day_offset > 0:
        view.add_item(Button(label="Previous Day", style=discord.ButtonStyle.secondary, custom_id=f"participants_prev_{day_offset - 1}"))
    if day_offset < 6:
        view.add_item(Button(label="Next Day", style=discord.ButtonStyle.secondary, custom_id=f"participants_next_{day_offset + 1}"))
    view.add_item(Button(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="back_to_menu"))

    # Edit the existing message instead of sending a new one
    await interaction.response.edit_message(embed=embed, view=view)

# --- Run the Bot ---
bot.run(TOKEN)
