import random

# Categorized quotes matching FocusOption names
QUOTES_DB = {
    "Active Rest": [
        "Rest is not a reward, it's a requirement for greatness. Tonight, release the stress. Let go of the pressure. Breathe out the noise of the day. — Anonymous",
        "The enjoyment of leisure would be nothing if we had only leisure. It is the joy of work well done that enables us to enjoy rest. — Elisabeth Elliot",
        "Work is a blessing. God has so arranged the world that work is necessary, and He gives us hands and strength to do it. — Elisabeth Elliot",
        "The quality of your life depends on making sleep a priority. You need to sleep to regenerate. — From Sleep Rituals",
        "Believe you can, and you're halfway there. — Theodore Roosevelt",
    ],
    "Cleaning": [
        "When your environment is clean, you feel happy, motivated, and healthy. — Lailah Gifty Akita",
        "The objective of cleaning is not just to clean, but to feel happiness living within that environment. — Marie Kondo",
        "Cleaning is a never-ending battle, but a battle worth fighting. — Anonymous",
        "A clean and tidy home really does contribute to a more peaceful living environment and a more restful mindset. — Anonymous",
        "Cleanliness is not next to godliness, it is godliness. — Mahatma Gandhi",
        "Better keep yourself clean and bright; you are the window through which you must see the world. — George Bernard Shaw",
    ],
    "Cooking": [
        "Cooking is like love. It should be entered into with abandon or not at all. — Harriet Van Horne",
        "Cooking at home shows such affection. In a bad economy, it's more important to make yourself feel good. — Ina Garten",
        "Cooking is at once child's play and adult joy. And cooking done with care is an act of love. — Craig Claiborne",
        "A recipe has no soul. You, as the cook, must bring soul to the recipe. — Thomas Keller",
        "I watch cooking change the cook, just as it transforms the food. — Laura Esquivel",
        "Learn how to cook...try new recipes, learn from your mistakes, be fearless, and above all have fun! — Julia Child",
    ],
    "Custom": [
        "To be yourself in a world that is constantly trying to make you something else is the greatest accomplishment. — Ralph Waldo Emerson",
        "Be yourself; everyone else is already taken. — Oscar Wilde",
        "To help yourself, you must be yourself. Be the best that you can be. — Dave Pelzer",
        "You change the world by being yourself. — Yoko Ono",
        "The courage to be the best version of yourself. — Sidney Poitier",
        "The freedom to be yourself is a gift only you can give yourself. But once you do, no one can take it away. — Doe Zantamata",
    ],
    "Exploring": [
        "We shall not cease from exploration, and the end of all our exploring will be to arrive where we started and know the place for the first time. — T.S. Eliot",
        "It is in our nature to explore, to reach out into the unknown. The only true failure would be not to explore at all. — Ernest Shackleton",
        "The real voyage of discovery consists not in seeking new landscapes, but in having new eyes. — Marcel Proust",
        "One's destination is never a place, but a new way of seeing things. — Henry Miller",
        "Nature is not a place to visit, it is home. — Gary Snyder",
        "In every walk with nature one receives far more than he seeks. — John Muir",
    ],
    "Family": [
        "It didn't matter how big our house was; it mattered that there was love in it. — Peter Buffett",
        "My life is proof that no matter what situation you're in, as long as you have a supportive family, you can achieve anything. — Michaela DePrince",
        "The way you help heal the world is you start with your own family. — Mother Teresa",
        "Families are the tie that reminds us of yesterday, provide strength and support today, and give us hope for tomorrow. — Bill Owens",
        "In family relationships love is really spelled t-i-m-e, time. — Dieter F. Uchtdorf",
    ],
    "Friends": [
        "Friendship is unnecessary, like philosophy, like art. It has no survival value; rather it is one of those things which give value to survival. — C.S. Lewis",
        "True friendship multiplies the good in life and divides its evils. — Baltasar Gracian",
        "A true friend is someone who sees the pain in your eyes while everyone else believes the smile on your face. — Anonymous",
        "Your friends will know you better in the first minute you meet than your acquaintances will know you in a thousand years. — Richard Bach",
        "Friendship is the source of the greatest pleasures, and without friends even the most agreeable pursuits become tedious. — Thomas Aquinas",
    ],
    "Fur Baby": [
        "Pets are humanizing. They remind us we have an obligation and responsibility to preserve and nurture and care for all life. — James Cromwell",
        "Animals are such agreeable friends – they ask no questions, they pass no criticisms. — George Eliot",
        "Be the person your dog thinks you are. — C.J. Frick",
        "A new dog never replaces an old dog, it merely expands the heart. — Erica Jong",
        "Owners of dogs will have noticed that, if you provide them with food and water and shelter and affection, they will think you are god. — Christopher Hitchens",
    ],
    "Hydration": [
        "Drinking water is essential to a healthy lifestyle. — Stephen Curry",
        "Water is life's matter and matrix, mother and medium. There is no life without water. — Albert Szent-Gyorgyi",
        "No one can know the infinite importance of a tiny drop of water better than a thirsty bird or a little ant or a man of desert! — Mehmet Murat ildan",
        "Right now, your most important assignment is YOU. Pour into yourself the way you nurture others. — Anonymous",
        "Drink water, but tell everyone it's wine. Stay hydrated and classy. — Anonymous",
        "Drink water, not because you're thirsty, but because you're committed to feeling your best. — Anonymous",
    ],
    "Learning": [
        "There is no end to education. It is not that you read a book, pass an examination, and finish with education. The whole of life, from the moment you are born to the moment you die, is a process of learning. — Jiddu Krishnamurti",
        "The purpose of learning is growth, and our minds, unlike our bodies, can continue growing as we continue to live. — Mortimer Adler",
        "In learning you will teach, and in teaching you will learn. — Phil Collins",
        "Learning never exhausts the mind. — Leonardo da Vinci",
        "Knowledge of languages is the doorway to wisdom. — Roger Bacon",
    ],
    "Meditation": [
        "Brilliant things happen in calm minds. Be calm. You're brilliant. — Headspace",
        "Meditation is bringing the mind home. — Sogyal Rinpoche",
        "Meditation practice is neither holding on nor avoiding; it is a settling back into the moment, opening to what is there. — Jack Kornfield",
        "Quiet the mind and the soul will speak. — Ma Jaya Sati Bhagavati",
        "With meditation, you begin to relax in your seat and just watch the movie of life. — Ken Wilber",
    ],
    "Organizing": [
        "Have nothing in your house that you do not know to be useful or believe to be beautiful. — William Morris",
        "Being organized is not about being company-ready 24/7. It's about being able to find what you need and having a calm, uncluttered space to deal with life's challenges. — Geralin Thomas",
        "For every minute spent in organizing, an hour is earned. — Benjamin Franklin",
        "Organizing is what you do before you do something so that your efforts are not wasted. — Richard Bach",
        "Out of clutter, find simplicity. — Albert Einstein",
    ],
    "Partner": [
        "The quality of your life ultimately depends on the quality of your relationships. — Esther Perel",
        "Being deeply loved by someone gives you strength, while loving someone deeply gives you courage. — Lao Tzu",
        "A great relationship is about two things: First, appreciating the similarities, and second, respecting the differences. — Anonymous",
        "The best thing to hold onto in life is each other. — Audrey Hepburn",
        "To the world you may be one person, but to one person you are the world. — Bill Wilson",
    ],
    "Power Walking": [
        "Walking is man's best medicine. — Hippocrates",
        "If you are in a bad mood, go for a walk. If you are still in a bad mood, go for another walk. — Hippocrates",
        "The beauty is in the walking — we are betrayed by destinations. — Gwyn Thomas",
        "In every walk with nature one receives far more than he seeks. — John Muir",
        "Walking for health and fitness, the easiest way to get in shape and stay in shape. — Frank Ring",
    ],
    "Reading": [
        "A reader lives a thousand lives before he dies. The man who never reads lives only one. — George R.R. Martin",
        "You think your pain and your heartbreak are unprecedented in the history of the world, but then you read. — James Baldwin",
        "The more that you read, the more things you will know. The more that you learn, the more places you'll go. — Dr. Seuss",
        "Books are a uniquely portable magic. — Stephen King",
        "To learn to read is to light a fire; every syllable that is spelled out is a spark. — Victor Hugo",
    ],
    "Relationships": [
        "When we love, we always strive to become better than we are. When we strive to become better than we are, everything around us becomes better too. — Paulo Coelho",
        "There is only one happiness in this life, to love and be loved. — George Sand",
        "The best thing to hold onto in life is each other. — Audrey Hepburn",
        "We can improve our relationships with others by leaps and bounds if we become encouragers instead of critics. — Joyce Meyer",
        "The more we trust, the farther we are able to venture. — Esther Perel",
    ],
    "Relaxation": [
        "I've decided to be happy because it is good for my health. — Voltaire",
        "Tension is who you think you should be. Relaxation is who you are. — Chinese Proverb",
        "Sometimes the most productive thing you can do is relax. — Mark Black",
        "You are the sky. Everything else—it's just the weather. — Pema Chödrön",
        "Calm your mind. Life becomes much easier when you keep your mind at peace. — Anonymous",
    ],
    "Solitude": [
        "I live in that solitude which is painful in youth, but delicious in the years of maturity. — Albert Einstein",
        "Be so fulfilled with yourself, that even when you are alone, you feel in good company. — Irini Zoica",
        "I find it wholesome to be alone the greater part of the time. To be in company, even with the best, is soon wearisome and dissipating. I love to be alone. — Henry David Thoreau",
        "By all means use sometimes to be alone. Salute thyself; see what thy soul doth wear. — George Herbert",
        "In solitude the mind gains strength and learns to lean upon itself. — Laurence Sterne",
    ],
    "Strength Training": [
        "Strength does not come from winning. Your struggles develop your strengths. When you go through hardships and decide not to surrender, that is strength. — Arnold Schwarzenegger",
        "The last three or four reps is what makes the muscle grow. This area of pain divides a champion from someone who is not a champion. — Arnold Schwarzenegger",
        "What hurts today makes you stronger tomorrow. — Jay Cutler",
        "You're only one workout away from a good mood. — Anonymous",
        "The groundwork for all happiness is good health. — Leigh Hunt",
    ],
    "Work": [
        "A dream doesn't become reality through magic; it takes sweat, determination and hard work. — Colin Powell",
        "No work is insignificant. All labor that uplifts humanity has dignity and importance and should be undertaken with painstaking excellence. — Martin Luther King Jr.",
        "Motivation will almost always beat mere talent. — Norman Ralph Augustine",
        "When we strive to become better than we are, everything around us becomes better too. — Paulo Coelho",
        "Coming together is a beginning, staying together is progress, and working together is a success. — Henry Ford",
    ],
    "Yoga": [
        "Yoga is a light, which once lit, will never dim. The better your practice, the brighter the flame. — B.K.S. Iyengar",
        "Yoga is like music: the rhythm of the body, the melody of the mind, and the harmony of the soul create the symphony of life. — B.K.S. Iyengar",
        "Great things are done by a series of small things brought together. — Vincent Van Gogh",
        "The pose begins when you want to leave it. — B.K.S. Iyengar",
        "Yoga does not just change the way we see things, it transforms the person who sees. — B.K.S. Iyengar",
    ],
}

def get_daily_quote(focus_topics=None):
    """
    Returns a random quote. 
    If focus_topics (list of strings) is provided, it attempts to find a quote 
    matching one of those topics.
    """
    
    # 1. Collect potential quotes
    candidate_quotes = []

    if focus_topics:
        # Try to find categories that match the user's topics
        for topic in focus_topics:
            if topic in QUOTES_DB:
                candidate_quotes.extend(QUOTES_DB[topic])
    
    # 2. If no valid topics or no quotes found for topics, fallback to ALL quotes
    if not candidate_quotes:
        for q_list in QUOTES_DB.values():
            candidate_quotes.extend(q_list)

    # 3. Pick one randomly
    if not candidate_quotes:
        return "Focus on the present moment."  # Fallback if DB is empty

    return random.choice(candidate_quotes)
