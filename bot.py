import os
import asyncio
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, User, UserSettings, Deck, Card, ReviewLog
from srs_engine import calculate_next_review, ReviewQuality
from seed_questions import SEED_QUESTIONS

# ⚠️ GANTI DENGAN TOKEN BOT KAMU!
TOKEN = "Your token bot here"


# Setup database
engine = create_engine("sqlite:///flashcard.db", echo=False)
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

# Setup bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# ==========================================
# SESSION MANAGEMENT
# ==========================================
active_sessions = {}


# ==========================================
# HELPER: Buat embed kartu
# ==========================================
def create_card_embed(card, deck_name, card_number, total_cards):
    embed = discord.Embed(
        title=f"📚 Kartu {card_number}/{total_cards}",
        description=f"❓ **{card.question}**\n\n*(Pikirkan jawabanmu, lalu klik tombol di bawah)*",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"📁 {deck_name}")
    return embed


# ==========================================
# HELPER: Tampilkan Stats
# ==========================================
async def show_stats(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if not user:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Gunakan `/register` dulu!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Gunakan `/register` dulu!", ephemeral=True)
            return

        total_reviews = db.query(ReviewLog).filter_by(user_id=user.id).count()
        reviewed_ids = [r[0] for r in db.query(ReviewLog.card_id).filter_by(user_id=user.id).distinct().all()]
        unique_cards = len(reviewed_ids)
        mastered = 0
        if reviewed_ids:
            mastered = db.query(Card).filter(Card.id.in_(reviewed_ids), Card.interval >= 7).count()

        total_decks = db.query(Deck).count()
        streak = user.streak if user.streak else 0

        if total_reviews >= 100:
            level = "🏆 Master Learner"
        elif total_reviews >= 50:
            level = "🥇 Advanced Learner"
        elif total_reviews >= 20:
            level = "🥈 Intermediate Learner"
        elif total_reviews >= 5:
            level = "🥉 Beginner Learner"
        else:
            level = "🌱 New Learner"

        embed = discord.Embed(
            title=f"📊 Statistik Belajar - {user.username}",
            color=discord.Color.gold()
        )
        embed.add_field(name="🔥 Streak", value=f"{streak} hari", inline=True)
        embed.add_field(name="📝 Total Review", value=str(total_reviews), inline=True)
        embed.add_field(name="📚 Kartu Dipelajari", value=str(unique_cards), inline=True)
        embed.add_field(name="🏆 Mastery", value=f"{mastered} kartu hafal", inline=True)
        embed.add_field(name="📁 Total Deck", value=str(total_decks), inline=True)
        embed.add_field(name="🎖️ Level", value=level, inline=False)
        embed.set_footer(text="💡 Review setiap hari untuk menjaga streak!")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        db.close()


# ==========================================
# VIEW: Dropdown Pilih Deck
# ==========================================
class DeckSelectView(discord.ui.View):
    def __init__(self, decks):
        super().__init__(timeout=300)
        options = []
        for d in decks:
            options.append(discord.SelectOption(label=d.name))
        self.select = discord.ui.Select(
            placeholder="📚 Pilih deck/tema yang ingin dipelajari...",
            options=options
        )
        self.select.callback = self.deck_selected
        self.add_item(self.select)

    async def deck_selected(self, interaction: discord.Interaction):
        deck_name = self.select.values[0]
        await start_study_session(interaction, deck_name)


# ==========================================
# VIEW: Tombol Setelah Kuis Selesai
# ==========================================
class SessionCompleteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="📚 Lanjut Deck Lain", style=discord.ButtonStyle.primary)
    async def btn_next_deck(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = SessionLocal()
        try:
            all_decks = db.query(Deck).all()
            if not all_decks:
                await interaction.response.edit_message(content="📭 Belum ada deck.", embed=None, view=None)
                return
            await interaction.response.edit_message(
                content="📚 **Pilih deck/tema yang ingin dipelajari:**",
                embed=None,
                view=DeckSelectView(all_decks)
            )
        finally:
            db.close()

    @discord.ui.button(label="📊 Lihat Stats", style=discord.ButtonStyle.success)
    async def btn_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await show_stats(interaction)


# ==========================================
# VIEW: Tombol Jawaban (dengan auto-lanjut)
# ==========================================
class ReviewButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    async def handle_review(self, interaction: discord.Interaction, quality: ReviewQuality):
        user_id = str(interaction.user.id)
        session = active_sessions.get(user_id)

        if not session:
            await interaction.response.edit_message(
                content="❌ Sesi belajar tidak ditemukan. Gunakan `/study` untuk mulai.",
                embed=None, view=None
            )
            return

        db = SessionLocal()
        try:
            current_index = session["index"]
            card_id = session["card_ids"][current_index]
            card = db.query(Card).filter_by(id=card_id).first()
            deck = db.query(Deck).filter_by(id=card.deck_id).first()

            if not card:
                await interaction.response.edit_message(content="❌ Kartu tidak ditemukan.", embed=None, view=None)
                return

            result = calculate_next_review(
                interval=card.interval,
                ease_factor=card.ease_factor,
                repetitions=card.repetitions,
                quality=quality
            )

            user = db.query(User).filter_by(discord_id=user_id).first()
            if not user:
                await interaction.response.edit_message(content="❌ User tidak ditemukan.", embed=None, view=None)
                return

            log = ReviewLog(
                card_id=card.id, user_id=user.id, quality=quality.value,
                interval_before=card.interval, interval_after=result["interval"],
                ease_before=card.ease_factor, ease_after=result["ease_factor"]
            )
            db.add(log)

            card.interval = result["interval"]
            card.ease_factor = result["ease_factor"]
            card.repetitions = result["repetitions"]
            card.next_review_date = result["next_review_date"]
            db.commit()

            quality_labels = {0: "🔴 Lupa", 1: "🟠 Susah", 2: "🟡 Cukup", 3: "🟢 Mudah"}
            feedback = (
                f"✅ **Jawaban:** {card.answer}\n"
                f"📖 **Penjelasan:** {card.explanation or 'Belum ada penjelasan.'}\n"
                f"Jawabanmu: {quality_labels[quality.value]} | "
                f"⏳ Interval baru: {result['interval']} hari\n"
                f"{'─' * 30}\n"
            )

            session["index"] += 1

            if session["index"] < len(session["card_ids"]):
                next_card_id = session["card_ids"][session["index"]]
                next_card = db.query(Card).filter_by(id=next_card_id).first()
                next_deck = db.query(Deck).filter_by(id=next_card.deck_id).first()

                card_number = session["index"] + 1
                total_cards = len(session["card_ids"])

                embed = create_card_embed(next_card, next_deck.name, card_number, total_cards)
                embed.description = feedback + embed.description

                await interaction.response.edit_message(embed=embed, view=ReviewButtons())
            else:
                # === STREAK LOGIC ===
                del active_sessions[user_id]
                today = datetime.utcnow().date()
                
                if user.last_review_date and user.last_review_date.date() == today:
                    streak_msg = f"🎁 **Bonus Session!** Streak tetap {user.streak} hari."
                elif user.last_review_date and (today - user.last_review_date.date()).days == 1:
                    user.streak = (user.streak or 0) + 1
                    streak_msg = f"🔥 **Streak {user.streak} hari!** Lanjutkan besok!"
                else:
                    user.streak = 1
                    streak_msg = f"🔥 **Streak Hari 1 dimulai!**"

                user.last_review_date = datetime.utcnow()
                db.commit()

                embed = discord.Embed(
                    title="🎉 Sesi Belajar Selesai!",
                    description=(
                        feedback +
                        f"Kamu telah menyelesaikan semua kartu di deck **{deck.name}**!\n\n"
                        f"{streak_msg}\n"
                        f"Mau lanjut ke deck lain atau lihat statistik?"
                    ),
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=embed, view=SessionCompleteView())

        except Exception as e:
            db.rollback()
            await interaction.response.edit_message(content=f"❌ Error: {e}", embed=None, view=None)
        finally:
            db.close()

    @discord.ui.button(label="🔴 Lupa", style=discord.ButtonStyle.danger)
    async def btn_forgot(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, ReviewQuality.FORGOT)

    @discord.ui.button(label="🟠 Susah", style=discord.ButtonStyle.secondary)
    async def btn_hard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, ReviewQuality.HARD)

    @discord.ui.button(label="🟡 Cukup", style=discord.ButtonStyle.primary)
    async def btn_good(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, ReviewQuality.GOOD)

    @discord.ui.button(label="🟢 Mudah", style=discord.ButtonStyle.success)
    async def btn_easy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, ReviewQuality.EASY)

    @discord.ui.button(label="🛑 Stop", style=discord.ButtonStyle.grey)
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        if user_id in active_sessions:
            del active_sessions[user_id]
        await interaction.response.edit_message(content="🛑 Sesi belajar dihentikan.", embed=None, view=None)


# ==========================================
# HELPER: Mulai sesi belajar
# ==========================================
async def start_study_session(interaction: discord.Interaction, deck_name: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if not user:
            await interaction.response.edit_message(content="❌ Gunakan `/register` dulu!", embed=None, view=None)
            return

        deck = db.query(Deck).filter_by(name=deck_name).first()
        if not deck:
            await interaction.response.edit_message(content=f"❌ Deck '{deck_name}' tidak ditemukan.", embed=None, view=None)
            return

        # Ambil SEMUA kartu di deck ini (tanpa filter tanggal)
        cards_due = db.query(Card).filter(
            Card.deck_id == deck.id
        ).limit(user.daily_new_limit).all()

        if not cards_due:
            await interaction.response.edit_message(
                content=f"📭 Deck **{deck_name}** belum punya kartu!",
                embed=None, view=None
            )
            return

        user_id = str(interaction.user.id)
        active_sessions[user_id] = {
            "card_ids": [c.id for c in cards_due],
            "index": 0,
            "deck_name": deck_name
        }

        card = cards_due[0]
        embed = create_card_embed(card, deck_name, 1, len(cards_due))
        await interaction.response.edit_message(embed=embed, view=ReviewButtons())

    finally:
        db.close()


# ==========================================
# EVENTS
# ==========================================
@bot.event
async def on_ready():
    print(f"✅ Bot online sebagai {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} command berhasil di-sync ke Discord")
    except Exception as e:
        print(f"❌ Gagal sync command: {e}")


# ==========================================
# COMMANDS
# ==========================================
@bot.tree.command(name="ping", description="Cek apakah bot hidup")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot hidup dan database tersambung.", ephemeral=True)


@bot.tree.command(name="register", description="Daftar akun flashcard")
async def register(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if existing:
            await interaction.response.send_message(f"⚠️ Kamu sudah terdaftar sebagai **{existing.username}**!", ephemeral=True)
            return
        
        new_user = User(discord_id=str(interaction.user.id), username=interaction.user.name, password_hash="DISCORD_AUTH")
        db.add(new_user)
        db.commit()
        
        # Auto-create settings
        settings = UserSettings(user_id=new_user.id)
        db.add(settings)
        db.commit()
        
        await interaction.response.send_message(f"✅ Registrasi berhasil! Selamat datang, **{new_user.username}**!", ephemeral=True)
    except Exception as e:
        db.rollback()
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    finally:
        db.close()


@bot.tree.command(name="login", description="Cek status login kamu")
async def login(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if not user:
            await interaction.response.send_message("❌ Gunakan `/register` dulu!", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Login sebagai **{user.username}** | Limit: {user.daily_new_limit} kartu/hari", ephemeral=True)
    finally:
        db.close()


@bot.tree.command(name="adddeck", description="Buat deck baru")
@app_commands.describe(name="Nama deck")
async def adddeck(interaction: discord.Interaction, name: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if not user:
            await interaction.response.send_message("❌ /register dulu!", ephemeral=True)
            return
        db.add(Deck(name=name, description="", user_id=user.id))
        db.commit()
        await interaction.response.send_message(f"✅ Deck **{name}** dibuat!", ephemeral=True)
    finally:
        db.close()


@bot.tree.command(name="addcard", description="Tambah kartu ke deck")
@app_commands.describe(deck_name="Nama deck", question="Pertanyaan", answer="Jawaban")
async def addcard(interaction: discord.Interaction, deck_name: str, question: str, answer: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if not user:
            await interaction.response.send_message("❌ /register dulu!", ephemeral=True)
            return
        deck = db.query(Deck).filter_by(name=deck_name, user_id=user.id).first()
        if not deck:
            await interaction.response.send_message(f"❌ Deck {deck_name} tidak ada.", ephemeral=True)
            return
        db.add(Card(question=question, answer=answer, deck_id=deck.id, next_review_date=datetime.utcnow()))
        db.commit()
        await interaction.response.send_message(f"✅ Kartu ditambahkan!", ephemeral=True)
    finally:
        db.close()


@bot.tree.command(name="decks", description="Lihat semua deck")
async def decks(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        all_decks = db.query(Deck).all()
        if not all_decks:
            await interaction.response.send_message("📭 Belum ada deck.", ephemeral=True)
            return
        lines = []
        for d in all_decks:
            count = db.query(Card).filter_by(deck_id=d.id).count()
            lines.append(f"📁 **{d.name}** — {count} kartu")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
    finally:
        db.close()


@bot.tree.command(name="study", description="Mulai sesi belajar flashcard")
async def study(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if not user:
            await interaction.response.send_message("❌ Gunakan `/register` dulu!", ephemeral=True)
            return

        all_decks = db.query(Deck).all()
        if not all_decks:
            await interaction.response.send_message("📭 Belum ada deck.", ephemeral=True)
            return

        await interaction.response.send_message(
            "📚 **Pilih deck/tema yang ingin dipelajari:**",
            view=DeckSelectView(all_decks),
            ephemeral=True
        )
    finally:
        db.close()


@bot.tree.command(name="stats", description="Lihat statistik belajar kamu")
async def stats(interaction: discord.Interaction):
    await show_stats(interaction)


# ==========================================
# VIEW: Halaman Pengaturan
# ==========================================
class SettingsView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="🎯 Toggle Smart Mix", style=discord.ButtonStyle.secondary)
    async def btn_smart_mix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_setting(interaction, "smart_mix_enabled", "Smart Daily Mix")

    @discord.ui.button(label="🤔 Toggle 'Kenapa?'", style=discord.ButtonStyle.secondary)
    async def btn_elaborative(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_setting(interaction, "elaborative_enabled", "Tanya Kenapa")

    @discord.ui.button(label="🔀 Toggle Interleaving", style=discord.ButtonStyle.secondary)
    async def btn_interleaving(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_setting(interaction, "interleaving_enabled", "Interleaving Practice")

    @discord.ui.button(label="🎯 Toggle Confidence", style=discord.ButtonStyle.secondary)
    async def btn_confidence(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_setting(interaction, "confidence_check_enabled", "Confidence Check")

    @discord.ui.button(label="🧘 Toggle Zen Mode", style=discord.ButtonStyle.secondary)
    async def btn_zen(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_setting(interaction, "zen_mode_default", "Zen Mode")

    @discord.ui.button(label="🍅 Toggle Pomodoro", style=discord.ButtonStyle.secondary)
    async def btn_pomodoro(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_setting(interaction, "pomodoro_enabled", "Pomodoro")

    @discord.ui.button(label="⬅️ Kembali", style=discord.ButtonStyle.grey)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = SessionLocal()
        try:
            user = db.query(User).filter_by(id=self.user_id).first()
            total_cards = db.query(Card).count()
            embed = discord.Embed(
                title="🤖 BotQuizz - Menu Utama",
                description=(
                    f"Halo, **{user.username}**! 👋\n\n"
                    f"🔥 Streak: **{user.streak or 0}** hari\n"
                    f"📚 Total kartu: **{total_cards}**\n\n"
                    f"Pilih menu di bawah untuk mulai!"
                ),
                color=discord.Color.blue()
            )
            embed.set_footer(text="BotQuizz - Belajar cerdas, bukan belajar keras! 💪")
            await interaction.response.edit_message(embed=embed, view=SystemMenuView())
        finally:
            db.close()

    async def toggle_setting(self, interaction: discord.Interaction, field: str, label: str):
        db = SessionLocal()
        try:
            settings = db.query(UserSettings).filter_by(user_id=self.user_id).first()
            if not settings:
                settings = UserSettings(user_id=self.user_id)
                db.add(settings)

            current = getattr(settings, field)
            setattr(settings, field, not current)
            db.commit()

            status = "✅ ON" if not current else "❌ OFF"
            await interaction.response.send_message(
                f"{label}: {status}", ephemeral=True
            )
        finally:
            db.close()


# ==========================================
# VIEW: Menu Utama /system
# ==========================================
class SystemMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="📚 Mulai Belajar", style=discord.ButtonStyle.primary)
    async def btn_study(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = SessionLocal()
        try:
            all_decks = db.query(Deck).all()
            if not all_decks:
                await interaction.response.edit_message(content="📭 Belum ada deck.", embed=None, view=None)
                return
            await interaction.response.edit_message(
                content="📚 **Pilih deck/tema yang ingin dipelajari:**",
                embed=None,
                view=DeckSelectView(all_decks)
            )
        finally:
            db.close()

    @discord.ui.button(label="📊 Statistik", style=discord.ButtonStyle.success)
    async def btn_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await show_stats(interaction)

    @discord.ui.button(label="📁 Lihat Deck", style=discord.ButtonStyle.secondary)
    async def btn_decks(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = SessionLocal()
        try:
            all_decks = db.query(Deck).all()
            if not all_decks:
                await interaction.response.edit_message(content="📭 Belum ada deck.", embed=None, view=None)
                return
            lines = []
            for d in all_decks:
                count = db.query(Card).filter_by(deck_id=d.id).count()
                lines.append(f"📁 **{d.name}** — {count} kartu")
            embed = discord.Embed(
                title="📁 Daftar Deck",
                description="\n".join(lines),
                color=discord.Color.blue()
            )
            await interaction.response.edit_message(embed=embed, view=SystemMenuView())
        finally:
            db.close()

    @discord.ui.button(label="❓ Panduan", style=discord.ButtonStyle.grey)
    async def btn_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="❓ Panduan Belajar",
            description=(
                "1. Klik **📚 Mulai Belajar** untuk memilih deck\n"
                "2. Baca pertanyaan, pikirkan jawabannya\n"
                "3. Klik tombol jawaban sesuai ingatanmu:\n"
                "   🔴 Lupa | 🟠 Susah | 🟡 Cukup | 🟢 Mudah\n"
                "4. Lihat jawaban asli & jadwal review berikutnya\n"
                "5. Lanjut otomatis ke kartu berikutnya!\n\n"
                "💡 **Tips:** Review setiap hari untuk menjaga streak 🔥"
            ),
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=SystemMenuView())
    
    @discord.ui.button(label="⚙️ Pengaturan", style=discord.ButtonStyle.secondary)
    async def btn_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = SessionLocal()
        try:
            user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
            if not user:
                await interaction.response.send_message("❌ Register dulu!", ephemeral=True)
                return

            # Ambil atau buat settings
            settings = db.query(UserSettings).filter_by(user_id=user.id).first()
            if not settings:
                settings = UserSettings(user_id=user.id)
                db.add(settings)
                db.commit()

            def status(val):
                return "✅ ON" if val else "❌ OFF"

            embed = discord.Embed(
                title="⚙️ Pengaturan Belajar",
                description=(
                    f"🎯 **Smart Daily Mix:** {status(settings.smart_mix_enabled)}\n"
                    f"🤔 **Tanya 'Kenapa?':** {status(settings.elaborative_enabled)}\n"
                    f"🔀 **Interleaving:** {status(settings.interleaving_enabled)}\n"
                    f"🎯 **Confidence Check:** {status(settings.confidence_check_enabled)}\n"
                    f"🧘 **Zen Mode:** {status(settings.zen_mode_default)}\n"
                    f"🍅 **Pomodoro:** {status(settings.pomodoro_enabled)}\n\n"
                    f"💡 Fitur inti (Penjelasan, Insight, Leaked Alert) selalu aktif.\n\n"
                    f"Klik tombol untuk toggle ON/OFF:"
                ),
                color=discord.Color.orange()
            )
            await interaction.response.edit_message(embed=embed, view=SettingsView(user.id))
        finally:
            db.close()


@bot.tree.command(name="system", description="Menu utama BotQuizz")
async def system(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(discord_id=str(interaction.user.id)).first()
        if not user:
            await interaction.response.send_message("❌ Gunakan `/register` dulu!", ephemeral=True)
            return

        total_cards = db.query(Card).count()

        embed = discord.Embed(
            title="🤖 BotQuizz - Menu Utama",
            description=(
                f"Halo, **{user.username}**! 👋\n\n"
                f"🔥 Streak: **{user.streak or 0}** hari\n"
                f"📚 Total kartu tersedia: **{total_cards}**\n\n"
                f"Pilih menu di bawah untuk mulai!"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="BotQuizz - Belajar cerdas, bukan belajar keras! 💪")
        await interaction.response.send_message(embed=embed, view=SystemMenuView(), ephemeral=True)
    finally:
        db.close()


# ==========================================
# SEED DATA
# ==========================================
def seed_data():
    db = SessionLocal()
    try:
        db.query(Card).delete()
        db.query(Deck).delete()
        db.query(User).delete()
        db.query(UserSettings).delete()
        db.commit()
        print("🗑️ Database dikosongkan...")

        now = datetime.utcnow()
        demo_user = User(discord_id="DEMO_USER", username="DemoUser", password_hash="DISCORD_AUTH")
        db.add(demo_user)
        db.commit()

        default_settings = UserSettings(user_id=demo_user.id)
        db.add(default_settings)
        db.commit()

        total = 0
        for deck_name, cards in SEED_QUESTIONS.items():
            deck = Deck(name=deck_name, description="", user_id=demo_user.id)
            db.add(deck)
            db.commit()
            for q, a, exp, tags in cards:
                db.add(Card(
                    question=q, answer=a, explanation=exp,
                    tags=tags, deck_id=deck.id, next_review_date=now
                ))
                total += 1
            db.commit()
            print(f"  📚 Deck '{deck_name}': {len(cards)} kartu")

        print(f"✅ SELESAI! {total} kartu dari {len(SEED_QUESTIONS)} deck berhasil ditambah.")
    except Exception as e:
        db.rollback()
        print(f"❌ ERROR SEED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


# ==========================================
# RUN BOT
# ==========================================
async def main():
    seed_data()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())