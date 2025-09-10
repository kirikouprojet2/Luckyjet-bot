#!/usr/bin/env python3
# analyse_luckyjet_advanced.py

from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
import logging
import statistics
from datetime import datetime
import math
import os
import time
from typing import List, Dict, Any

# Configuration - TON NOUVEAU TOKEN EST ICI !
TOKEN = "8424487445:AAEYyxgGMGWdyuh7TCHZ77aY3wJ66FAQrBU"
PORT = os.getenv('PORT', 8443)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
start_time = datetime.now()

# ----- utilitaires statistiques -----
def safe_floats(list_str: List[str]) -> List[float]:
    vals = []
    for s in list_str:
        try:
            cleaned_s = s.strip().replace(',', '.').replace('x', '').replace('X', '')
            v = float(cleaned_s)
            if 0.1 < v < 100:
                vals.append(v)
        except (ValueError, TypeError):
            continue
    return vals

def remove_outliers_iqr(values: List[float]) -> List[float]:
    if len(values) < 4:
        return values[:]
    try:
        s = sorted(values)
        q1 = statistics.quantiles(s, n=4)[0]
        q3 = statistics.quantiles(s, n=4)[2]
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        cleaned = [v for v in values if lower <= v <= upper]
        if len(cleaned) < max(3, int(len(values) * 0.6)):
            return values[:]
        return cleaned
    except Exception:
        return values[:]

def weighted_recent_mean(values: List[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    weights = [math.exp(i / n) for i in range(n)]
    total_w = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total_w

def longest_consecutive_below(values: List[float], threshold: float = 1.5) -> int:
    longest = 0
    current = 0
    for v in values:
        if v < threshold:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest

def volatility_score(stdev: float) -> float:
    if stdev <= 0:
        return 95
    if stdev < 0.4:
        return 90 + (0.4 - stdev) * 12.5
    if stdev < 1.0:
        return 60 + (1.0 - stdev) * 50
    if stdev < 2.0:
        return 20 + (2.0 - stdev) * 40
    if stdev < 4.0:
        return 5 + (4.0 - stdev) * 7.5
    return 3

def compute_confidence(stdev: float, pct_low: float, count: int, longest_low: int) -> float:
    vol_score = volatility_score(stdev)
    confidence = vol_score
    if pct_low > 70:
        confidence -= 15
    elif pct_low < 30:
        confidence += 10
    if longest_low >= 4:
        confidence += 12
    elif longest_low >= 3:
        confidence += 8
    if count > 15:
        confidence += 10
    elif count > 10:
        confidence += 5
    return max(5, min(98, round(confidence, 1)))

def analyse_and_decide(raw_list: List[str]) -> Dict[str, Any]:
    vals = safe_floats(raw_list)
    if len(vals) < 5:
        raise ValueError("Il faut au moins 5 valeurs valides (10 recommandé).")
    
    count = len(vals)
    mini = min(vals)
    maxi = max(vals)
    mean = round(statistics.mean(vals), 2)
    median = round(statistics.median(vals), 2)
    stdev = round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0
    pct_low = round((sum(1 for v in vals if v < 1.5) / count) * 100, 1)
    
    cleaned = remove_outliers_iqr(vals)
    cleaned_mean = round(statistics.mean(cleaned), 2) if cleaned else mean
    cleaned_stdev = round(statistics.pstdev(cleaned), 2) if len(cleaned) > 1 else stdev
    
    wmean = round(weighted_recent_mean(vals), 2)
    longest_low = longest_consecutive_below(vals, 1.5)
    
    base_prediction = (cleaned_mean * 0.5) + (wmean * 0.3) + (median * 0.2)
    
    if longest_low >= 3 and pct_low >= 40:
        base_prediction *= 1.25
    if cleaned_stdev > 1.5:
        base_prediction *= 0.9
    elif cleaned_stdev < 0.5:
        base_prediction *= 1.1
    
    prediction = round(max(1.5, min(base_prediction, 10.0)), 2)
    confidence = compute_confidence(cleaned_stdev, pct_low, count, longest_low)
    
    if confidence >= 80 and pct_low < 60 and prediction >= 1.8:
        signal = "🟢"
        stake = "Mise : ✅ FORTE"
        cashout = f"Objectif : x{prediction}"
    elif confidence >= 65:
        signal = "🟠"
        stake = "Mise : ⚠️ MOYENNE"
        cashout = f"Objectif : x{max(1.5, round(prediction * 0.8, 2))}"
    else:
        signal = "🔴"
        stake = "Mise : 🔴 ÉVITER"
        cashout = "Objectif : x1.50 (seuil minimum)"
    
    return {
        "count": count, "mean": mean, "median": median, "min": mini, "max": maxi,
        "stdev": stdev, "cleaned_mean": cleaned_mean, "cleaned_stdev": cleaned_stdev,
        "wmean": wmean, "pct_low": pct_low, "longest_low": longest_low,
        "prediction": prediction, "confidence": confidence, "signal": signal,
        "stake": stake, "cashout": cashout, "values_sample": vals[-5:]
    }

# ----- Handlers Telegram -----
def start(update: Update, context: CallbackContext):
    update.message.reply_text("""
🎯 *LuckyJet Analyse Avancée* 🎯

📊 *Commandes:*
/start - Aide
/analyse - Analyse multiplicateurs
/help - Aide détaillée
/stats - Stats du bot

📝 *Format:*
/analyse 1.5,2.0,3.2,1.1,1.0

💡 *5 valeurs minimum requis*
""", parse_mode="Markdown")

def analyse(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("❌ Format: /analyse 1.5,2.0,3.1,1.0,2.5")
        return
    try:
        raw_text = " ".join(context.args)
        parts = [part for part in raw_text.replace(';', ',').split(',') if part.strip()]
        if len(parts) < 5:
            update.message.reply_text("⚠️ Minimum 5 valeurs requis")
            return
        results = analyse_and_decide(parts)
        heure = datetime.now().strftime("%H:%M")
        text = f"""
📊 *Rapport d'Analyse* — {heure}

📈 *Stats:*
• Valeurs: {results['count']}
• Moyenne: x{results['mean']}
• Médiane: x{results['median']}
• Min/Max: x{results['min']}/x{results['max']}

🔍 *Métriques:*
• % < x1.50: {results['pct_low']}%
• Série basse: {results['longest_low']}

🎯 *RECOMMANDATION:*
{results['signal']} *Prédiction: x{results['prediction']}*
🔒 *Confiance: {results['confidence']}%*

💶 *Stratégie:*
{results['stake']}
{results['cashout']}

⚠️ *Aide à la décision seulement*
"""
        update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        update.message.reply_text("❌ Erreur: vérifiez le format")

def help_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("""
🆘 *Aide LuckyJet*

📋 *Format:*
/analyse 1.5,2.0,3.1,1.0,2.5,1.8

🔢 *Minimum:* 5 valeurs
✅ *Optimal:* 10-15 valeurs
""", parse_mode="Markdown")

def stats_cmd(update: Update, context: CallbackContext):
    uptime = datetime.now() - start_time
    hours, remainder = divmod(uptime.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    update.message.reply_text(f"""
🤖 *Statistiques du Bot*

• Uptime: {int(hours)}h {int(minutes)}m
• Démarrage: {start_time.strftime('%d/%m %H:%M')}
• Plateforme: Railway.app
• Version: 2.0 mobile
""", parse_mode="Markdown")

def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Erreur: {context.error}")

def main():
    try:
        updater = Updater(TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("analyse", analyse))
        dp.add_handler(CommandHandler("help", help_cmd))
        dp.add_handler(CommandHandler("stats", stats_cmd))
        dp.add_error_handler(error_handler)
        logger.info("🤖 Bot démarré avec le nouveau token...")
        updater.start_polling()
        updater.idle()
    except Exception as e:
        logger.critical(f"Erreur: {e}")

if __name__ == "__main__":
    main()
