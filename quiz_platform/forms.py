from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError

from .models import Question, Quiz, UserProfile, email_taken


class QuizForm(forms.ModelForm):
    # Форма создания и редактирования викторины (Create / Update).
    class Meta:
        model = Quiz
        fields = ['title', 'description', 'time_limit_minutes', 'is_published']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def clean_title(self):
        title = self.cleaned_data['title'].strip()
        if len(title) < 3:
            raise ValidationError('Название должно содержать минимум 3 символа.')
        return title


class QuestionForm(forms.ModelForm):
    # Форма добавления вопроса с четырьмя вариантами ответа.
    OPTION_FIELDS = ('option_a', 'option_b', 'option_c', 'option_d')

    class Meta:
        model = Question
        fields = ['text', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_option', 'points']
        widgets = {'text': forms.Textarea(attrs={'rows': 3})}

    def clean(self):
        cleaned = super().clean()
        values = [cleaned.get(name) for name in self.OPTION_FIELDS]
        if all(values):
            normalized = [v.strip().lower() for v in values]
            if len(set(normalized)) < len(normalized):
                raise ValidationError('Варианты ответа не должны повторяться.')
        return cleaned


class AttemptForm(forms.Form):
    # Форма прохождения: по одному полю-radio на каждый вопрос викторины.
    def __init__(self, quiz, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for number, question in enumerate(quiz.questions.all(), start=1):
            self.fields[f'q_{question.pk}'] = forms.ChoiceField(
                label=f'{number}. {question.text} ({question.points} б.)',
                choices=question.options,
                widget=forms.RadioSelect,
                required=False,
            )

    def get_answers(self):
        # {"<id вопроса>": "A"} - только отвеченные вопросы.
        return {name[2:]: value for name, value in self.cleaned_data.items() if value}


class ProfileForm(forms.ModelForm):
    # Профиль: аватарка и описание.
    MAX_AVATAR_SIZE = 2 * 1024 * 1024

    email = forms.EmailField(
        label='Почта', required=False,
        help_text='Привяжите почту: на неё придёт письмо со ссылкой подтверждения.')
    field_order = ['avatar', 'email', 'bio']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['email'].initial = self.instance.user.email

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if email and email_taken(email, exclude_user=self.instance.user):
            raise ValidationError('Эта почта уже подтверждена другим аккаунтом.')
        return email

    class Meta:
        model = UserProfile
        fields = ['avatar', 'bio']
        labels = {'avatar': 'Изменить аватар', 'bio': 'О себе'}
        help_texts = {'avatar': 'JPG, PNG, GIF или WEBP, до 2 МБ.'}
        widgets = {
            # Простое поле выбора файла: без строки «На данный момент» и флажка «Очистить».
            'avatar': forms.FileInput(attrs={'accept': 'image/*'}),
            'bio': forms.Textarea(attrs={'rows': 4, 'maxlength': 300}),
        }

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        if avatar and getattr(avatar, 'size', 0) > self.MAX_AVATAR_SIZE:
            raise ValidationError('Файл слишком большой: максимум 2 МБ.')
        return avatar


class ReviewForm(forms.Form):
    # Отзыв о викторине (сохраняется в поле review попытки прохождения).
    review = forms.CharField(
        label='Ваш отзыв', min_length=3, max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 3, 'maxlength': 500, 'placeholder': 'Что понравилось, что можно улучшить?'}),
    )


class EmailForm(forms.Form):
    email = forms.EmailField(label='Почта, указанная при регистрации')


class RegisterForm(UserCreationForm):
    # Регистрация по почте: адрес обязателен, аккаунт активируется после перехода по ссылке из письма.
    email = forms.EmailField(label='Почта', help_text='На этот адрес придёт письмо со ссылкой подтверждения.')

    class Meta(UserCreationForm.Meta):
        fields = ('username', 'email')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if email_taken(email):
            raise ValidationError('Эта почта уже подтверждена другим аккаунтом.')
        return email


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': ('Неверное имя пользователя или пароль. Если вы только что зарегистрировались, '
                          'сначала подтвердите почту по ссылке из письма.'),
    }
