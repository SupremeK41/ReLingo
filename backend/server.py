# from fastapi import FastAPI, HTTPException, status
# from fastapi.middleware.cors import CORSMiddleware
# from models import UserClass, UserDAL
# from motor.motor_asyncio import AsyncIOMotorClient
# import uvicorn
# from contextlib import asynccontextmanager
# from pydantic import BaseModel
# from dotenv import load_dotenv
# load_dotenv()  # Loads the variables from the .env file into os.environ
# import os
# import sys

# COLLECTION_NAME = 'ReLingo'
# MONGODB_URI = os.environ["MONGODB_URI"]
# DEBUG = os.environ.get("DEBUG", "").strip().lower() in {"1", "true", "on", "yes"}

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     # Startup:
#     client = AsyncIOMotorClient(MONGODB_URI)
#     database = client.get_default_database()
#     pong = await database.command("ping")
#     if int(pong["ok"]) != 1:
#         raise Exception("Cluster connection is not okay!")
#     todo_lists = database.get_collection(COLLECTION_NAME)
#     app.user_dal = UserDAL(todo_lists)
#     # Yield back to FastAPI Application:
#     yield
#     # Shutdown:
#     client.close()

# app = FastAPI(lifespan=lifespan)

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# @app.get("/api/get_score/{user_id}", response_model=UserClass)
# async def get_user(user_id: str) -> UserClass:
#     response = await app.user_dal.get_user(user_id)
#     if response is None:
#         raise HTTPException(status_code=404, detail="User not found")
#     # Convert the MongoDB ObjectId to a string
#     response.id = str(response.id)
#     return response

# class NewUser(BaseModel):
#     name: str
    
# class NewUserResponse(BaseModel):
#     id: str
#     name: str
    
# @app.post("/api/create_user", response_model=NewUserResponse)
# async def create_user(new_user: NewUser) -> NewUserResponse:
#     user_id = await app.user_dal.create_user(new_user.name)
#     # Convert the returned ObjectId to a string
#     return NewUserResponse(
#         id=str(user_id),
#         name=new_user.name
#     )

# @app.patch("/api/update_score/{user_id}", response_model=UserClass)
# async def update_score(user_id: str, score: int) -> UserClass:
#     result = await app.user_dal.update_score_user(user_id, score)
#     if result is None:
#         raise HTTPException(status_code=404, detail="User not found")
#     # Convert the MongoDB ObjectId to a string
#     result.id = str(result.id)
#     return result

# def main(argv=sys.argv[1:]):
#     try:
#         uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=DEBUG)
#     except KeyboardInterrupt:
#         pass

# if __name__ == "__main__":
#     main()

from fastapi import FastAPI
from tortoise.contrib.fastapi import register_tortoise
from models import (user_pydanticIn, user_pydantic, User)

# email
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from starlette.responses import JSONResponse
from starlette.requests import Request
from pydantic import BaseModel, EmailStr
from typing import ContextManager, List


# dotenv
from dotenv import dotenv_values

# adding cors headers
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()

# adding cors urls

origins = [
    'http://localhost:3000'
]

# add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins = origins,
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"]
)


@app.get('/')
def index():
    return {"Msg": "go to /docs for the API documentation"}


@app.post('/user')
async def add_supplier(user_info: user_pydanticIn):
    user_obj = await User.create(**user_info.dict(exclude_unset=True))
    response = await user_pydantic.from_tortoise_orm(user_obj)
    return {"status": "ok", "data" : response}
    
@app.get('/user')
async def get_all_suppliers():
    response = await user_pydantic.from_queryset(User.all())
    return {"status": "ok", "data": response}
    
@app.get('/user/{id}')
async def get_specific_user(id: int):
    user_obj = await User.get(id = id)
    response = await user_pydantic.from_tortoise_orm(user_obj)
    return {"status": "ok", "data": response}

# @app.put('/supplier/{supplier_id}')
# async def update_user_score(supplier_id: int, update_info: user_pydanticIn):
#     supplier = await Supplier.get(id=supplier_id)
#     update_info = update_info.dict(exclude_unset=True)
#     supplier.name = update_info['name']
#     supplier.company = update_info['company']
#     supplier.phone = update_info['phone']
#     supplier.email = update_info['email']
#     await supplier.save()
#     response = await supplier_pydantic.from_tortoise_orm(supplier)
#     return {"status": "ok", "data": response}


# @app.post('/product/{supplier_id}')
# async def add_product(supplier_id: int, products_details: product_pydanticIn):
#     supplier = await Supplier.get(id = supplier_id)
#     products_details = products_details.dict(exclude_unset = True)
#     products_details['revenue'] += products_details['quantity_sold'] * products_details['unit_price']
#     product_obj  = await Product.create(**products_details, supplied_by = supplier)
#     response = await product_pydantic.from_tortoise_orm(product_obj)
#     return {"status": "ok", "data": response}


@app.put('/user/{id}')
async def update_product(id: int, update_info: user_pydanticIn):
    user_obj = await User.get(id = id)
    update_info = update_info.dict(exclude_unset = True)
    user_obj.name = update_info['name']
    user_obj.best_score = max(update_info['best_score'], user_obj.best_score)
    user_obj.total_exp += update_info['score'] 
    await user_obj.save()
    response = await user_pydantic.from_tortoise_orm(user_obj)
    return {"status": "ok", "data": response}

@app.delete('/user/{id}')
async def delete_product(id: int):
    await User.filter(id = id).delete()
    return {"status": "ok"}



register_tortoise(
    app,
    db_url="sqlite://database.sqlite3",
    modules={"models": ["models"]},
    generate_schemas=True,
    add_exception_handlers=True
)