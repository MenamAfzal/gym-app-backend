# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from apps.nutritionX.models import WaterIntake
# from apps.food_logger.models import LoggedMeal
# from apps.reward_system.utils import process_automated_challenge
# 
# @receiver(post_save, sender=WaterIntake)
# def water_intake_challenge_trigger(sender, instance, created, **kwargs):
#     if created:
#         process_automated_challenge(
#             user=instance.user,
#             challenge_type='water_logging',
#             increment_by=instance.amount_ml
#         )
# 
# @receiver(post_save, sender=LoggedMeal)
# def food_logging_challenge_trigger(sender, instance, created, **kwargs):
#     if created:
#         process_automated_challenge(
#             user=instance.user,
#             challenge_type='food_logging',
#             increment_by=1
#         )
