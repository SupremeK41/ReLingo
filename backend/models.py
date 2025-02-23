# 
from tortoise.models import Model
from tortoise import fields
from tortoise.contrib.pydantic import pydantic_model_creator


class User(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=30, nullable=False)
    total_exp = fields.IntField(default=0)
    streak = fields.IntField(default=1)
    best_score = fields.IntField(max_digits=8, decimal_places=2, default=0)
    score = fields.IntField(default=0)
    


user_pydantic = pydantic_model_creator(User, name="User")
user_pydanticIn = pydantic_model_creator(User, name="UserIn", exclude_readonly=True)