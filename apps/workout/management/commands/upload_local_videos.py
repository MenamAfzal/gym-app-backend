import os
import re
import difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand
from apps.workout.models import Exercise
from apps.workout.local_storage import save_file_locally

class Command(BaseCommand):
    help = 'Copy local video files to media directory and link them to Exercises'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dir',
            type=str,
            required=True,
            help='Absolute path to the directory containing video files'
        )
        parser.add_argument(
            '--threads',
            type=int,
            default=10,
            help='Number of concurrent processing threads (default: 10)'
        )

    def get_canonical_name(self, name):
        if not name: return ""
        name = name.lower()
        name = name.replace('&', 'and').replace('+', 'and')
        
        digit_map = {
            '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
            '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
        }
        for digit, word in digit_map.items():
            name = name.replace(digit, word)
            
        return re.sub(r'[^a-z]', '', name)

    def process_file(self, file_path, exercise_id, exercise_name):
        filename = os.path.basename(file_path)
        
        try:
            rel_path, file_url = save_file_locally(file_path, folder="videos", filename=filename)

            Exercise.objects.filter(id=exercise_id).update(
                video_url=file_url,
                video_file=rel_path,
                upload_status='uploaded'
            )

            return True, f"Stored locally: {filename} -> {exercise_name} ({file_url})"

        except Exception as e:
            return False, f"Error processing {filename}: {str(e)}"

    def handle(self, *args, **options):
        directory = options['dir']
        max_threads = options['threads']

        if not os.path.isdir(directory):
            self.stdout.write(self.style.ERROR(f"Directory not found: {directory}"))
            return

        self.stdout.write(self.style.SUCCESS(f"Scanning directory: {directory}"))

        all_exercises = Exercise.objects.only('id', 'name', 'video_url')
        exercise_map = {}
        canonical_to_real_name = {}
        
        for ex in all_exercises:
            c_name = self.get_canonical_name(ex.name)
            if c_name:
                exercise_map[c_name] = ex
                canonical_to_real_name[c_name] = ex.name

        tasks = []
        supported_exts = ('.mp4', '.mov', '.m4v', '.avi', '.mkv')
        
        files = [f for f in os.listdir(directory) if f.lower().endswith(supported_exts)]
        self.stdout.write(f"Found {len(files)} video files.")

        unmatched_files = []

        for filename in files:
            name_part = os.path.splitext(filename)[0]
            canon_name = self.get_canonical_name(name_part)
            
            matched_ex = exercise_map.get(canon_name)
            
            if matched_ex:
                tasks.append({
                    'file_path': os.path.join(directory, filename),
                    'exercise_id': matched_ex.id,
                    'exercise_name': matched_ex.name
                })
            else:
                unmatched_files.append(filename)

        self.stdout.write(self.style.WARNING(f"Matched {len(tasks)} files to Exercises."))
        self.stdout.write(self.style.NOTICE(f"Unmatched {len(unmatched_files)} files."))

        if tasks:
            self.stdout.write(f"Starting local processing with {max_threads} threads...")
            success_count = 0
            failure_count = 0

            with ThreadPoolExecutor(max_workers=max_threads) as executor:
                future_to_file = {
                    executor.submit(self.process_file, t['file_path'], t['exercise_id'], t['exercise_name']): t 
                    for t in tasks
                }

                for future in as_completed(future_to_file):
                    success, message = future.result()
                    if success:
                        success_count += 1
                        self.stdout.write(self.style.SUCCESS(message))
                    else:
                        failure_count += 1
                        self.stdout.write(self.style.ERROR(message))
            
            self.stdout.write(self.style.SUCCESS(f"\nImport Completed! Success: {success_count}, Failed: {failure_count}"))

        if unmatched_files:
            self.stdout.write(self.style.WARNING("\n" + "="*50))
            self.stdout.write(self.style.WARNING("       UNMATCHED FILES REPORT"))
            self.stdout.write(self.style.WARNING("="*50))
            self.stdout.write("Most likely matches based on filename similarity:\n")

            all_canonical_names = list(exercise_map.keys())

            for filename in unmatched_files:
                name_part = os.path.splitext(filename)[0]
                canon_name = self.get_canonical_name(name_part)
                
                matches = difflib.get_close_matches(canon_name, all_canonical_names, n=3, cutoff=0.5)
                
                self.stdout.write(f"\n[?] File: {filename}")
                if matches:
                    for m in matches:
                        real_name = canonical_to_real_name.get(m, "Unknown")
                        self.stdout.write(f"    -> Did you mean: '{real_name}'?")
                else:
                    self.stdout.write("    -> No close match found.")

            self.stdout.write("\n" + "="*50)
