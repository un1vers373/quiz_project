from rest_framework import serializers

from .models import Question, Quiz, QuizAttempt


class QuizSerializer(serializers.ModelSerializer):
    # Эндпоинт 1: список викторин.
    created_by = serializers.CharField(source='created_by.username', read_only=True)
    question_count = serializers.IntegerField(read_only=True)
    total_points = serializers.IntegerField(read_only=True)

    class Meta:
        model = Quiz
        fields = ['id', 'title', 'description', 'time_limit_minutes', 'is_published',
                  'question_count', 'total_points', 'created_by']


class QuestionPublicSerializer(serializers.ModelSerializer):
    # Вопрос БЕЗ правильного ответа (его нельзя отдавать студенту).
    class Meta:
        model = Question
        fields = ['id', 'text', 'option_a', 'option_b', 'option_c', 'option_d', 'points']


class AttemptStartSerializer(serializers.ModelSerializer):
    # Ответ на старт попытки: id попытки, время, вопросы.
    time_limit_seconds = serializers.IntegerField(read_only=True)
    remaining_seconds = serializers.SerializerMethodField()
    questions = serializers.SerializerMethodField()

    class Meta:
        model = QuizAttempt
        fields = ['id', 'quiz', 'started_at', 'time_limit_seconds', 'remaining_seconds', 'questions']

    def get_remaining_seconds(self, obj):
        return obj.remaining_seconds()

    def get_questions(self, obj):
        return QuestionPublicSerializer(obj.quiz.questions.all(), many=True).data


class AnswerSubmitSerializer(serializers.Serializer):
    # Эндпоинт 2 (вход): {"answers": {"<id вопроса>": "A|B|C|D"}}
    answers = serializers.DictField(child=serializers.ChoiceField(choices=['A', 'B', 'C', 'D']))

    def validate_answers(self, value):
        if not all(str(key).isdigit() for key in value):
            raise serializers.ValidationError('Ключи answers должны быть id вопросов.')
        return value


class AttemptResultSerializer(serializers.ModelSerializer):
    # Эндпоинт 2 (выход): результат попытки.
    max_score = serializers.IntegerField(read_only=True)
    duration_seconds = serializers.FloatField(read_only=True)
    detail = serializers.SerializerMethodField()
    wrong_answers = serializers.SerializerMethodField()

    class Meta:
        model = QuizAttempt
        fields = ['id', 'quiz', 'score', 'max_score', 'is_valid', 'started_at',
                  'finished_at', 'duration_seconds', 'detail', 'wrong_answers']

    def get_detail(self, obj):
        if obj.is_valid:
            return 'Попытка засчитана.'
        return 'Попытка аннулирована: превышен лимит времени. Начислено 0 баллов.'

    def get_wrong_answers(self, obj):
        # Разбор ошибок: вопрос, ответ студента и правильный вариант.
        return [
            {'question_id': w['question_id'], 'text': w['text'],
             'your_answer': w['given'], 'correct_answer': w['correct']}
            for w in obj.wrong_answers()
        ]


class LeaderboardEntrySerializer(serializers.Serializer):
    # Эндпоинт 3: таблица лидеров.
    rank = serializers.IntegerField()
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    total_score = serializers.IntegerField()
    quizzes_done = serializers.IntegerField()
    total_time = serializers.FloatField()
    avatar = serializers.CharField(allow_blank=True)
