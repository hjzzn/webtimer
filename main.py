import os
import ssl
import json
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from apscheduler.schedulers.background import BackgroundScheduler
import random  # 1. 必须导入这个随机数库

# 引入 MQTT 核心通信库
try:
    import paho.mqtt.client as mqtt

    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

# --- 1. SQLite 数据库配置 ---
DB_PATH = os.path.join(os.path.dirname(__file__), "tasks.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class TaskModel(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    time_value = Column(String, nullable=False)
    device_name = Column(String, nullable=False)
    command = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class DeviceModel(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    uuid = Column(String, nullable=False, unique=True)
    device_type = Column(String, nullable=False)
    location = Column(String, nullable=False)
    status = Column(String, nullable=False)
    data = Column(String, nullable=True)


Base.metadata.create_all(bind=engine)

# --- 2. 您专属的云端 MQTT 核心安全总线配置 ---
mqtt_client = None
MQTT_BROKER = "u1617077.ala.asia-southeast1.emqxsl.com"  # 您的专属 EMQX 接入点
MQTT_PORT = 8883  # 强制使用 TLS/SSL 加密端口

# 🔐 您提供的真实认证账号密码
MQTT_USER = "hjzzn"
MQTT_PASS = "netzzn"

# 1. 生成 100 到 999 之间的随机三位数
random_suffix = random.randint(100, 999)
# 2. 用字符串格式化（f-string）把随机数拼接到你的基础 ID 后面
MY_CLIENT_ID = f"Home_IoT_Gateway_Backend_{random_suffix}"

# 证书文件名
CA_CERT_NAME = "emqxsl-ca.crt"


# MQTT 连接成功回调
def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        print("🟢 [MQTT 安全通道] 成功登录并连接至云端 EMQX 服务器，安全握手完毕！")
    else:
        print(f"❌ [MQTT 安全通道] 登录失败，错误码: {rc}。请核对账号密码或云端ACL权限。")


def sendCommandToMqtt(topic: str, payload_str: str):
    """
    通过加密总线外发物联网指令（已修改为动态主题与纯文本内容模式）
    """
    global mqtt_client

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n[{now_str}] 🚀 ====== MQTT 加密总线外发 ======")
    print(f" 发送主题 (Topic): {topic}")
    print(f" 发送内容 (Payload): {payload_str}")

    if MQTT_AVAILABLE and mqtt_client and mqtt_client.is_connected():
        try:
            # 发送纯文本内容 (ON/OFF)，开启 QoS=1 工业级送达保证
            info = mqtt_client.publish(topic, payload_str, qos=1)
            info.wait_for_publish()
            print(f" 状态反馈: ✨ 已成功发射并加密送达云端")
        except Exception as e:
            print(f" 状态反馈: ❌ 物理网络发送异常: {e}")
    else:
        print(" 状态反馈: ⚠️ 发送失败！MQTT 客户端正处于断开状态或未安装相关依赖库")

    print(f"=========================================\n")


# --- 3. APScheduler 定时任务引擎 ---
scheduler = BackgroundScheduler()


def execute_task(task_name: str, device_name: str, command: str):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == device_name).first()
    db.close()

    print(f"\n⏰ 定时排期触发 -> 事项: {task_name} | 目标: {device_name}")
    if device and device.device_type == "开关器" and command in ["ON", "OFF"]:
        # 动态构造主题，内容直接发 ON 或 OFF 字符串
        dynamic_topic = f"SWITCH/{device.uuid}/POWER"
        sendCommandToMqtt(dynamic_topic, command)
    else:
        print(f"常规指令执行: {command}")


def execute_countdown_task(device_name: str, command: str):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == device_name).first()
    db.close()

    print(f"\n⏳ 倒计时终点到达 -> 目标: {device_name}")
    if device:
        # 动态构造主题，内容直接发 ON 或 OFF 字符串
        dynamic_topic = f"SWITCH/{device.uuid}/POWER"
        sendCommandToMqtt(dynamic_topic, command)


def add_task_to_scheduler(task_id: int, name: str, task_type: str, time_value: str, device_name: str, command: str,
                          is_active: bool):
    job_id = str(task_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not is_active:
        return
    try:
        if task_type == 'once':
            run_time = datetime.strptime(time_value, '%Y-%m-%d %H:%M')
            scheduler.add_job(execute_task, 'date', run_date=run_time, args=[name, device_name, command], id=job_id)
        elif task_type == 'minute':
            scheduler.add_job(execute_task, 'interval', minutes=int(time_value), args=[name, device_name, command],
                              id=job_id)
        elif task_type == 'hour':
            hours, minute = map(int, time_value.split(' '))
            scheduler.add_job(execute_task, 'cron', hour=f"*/{hours}", minute=minute, args=[name, device_name, command],
                              id=job_id)
        elif task_type == 'daily':
            hour, minute = map(int, time_value.split(':'))
            scheduler.add_job(execute_task, 'cron', hour=hour, minute=minute, args=[name, device_name, command],
                              id=job_id)
        elif task_type == 'weekly':
            dow, time_str = time_value.split(' ')
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(execute_task, 'cron', day_of_week=dow, hour=hour, minute=minute,
                              args=[name, device_name, command], id=job_id)
        elif task_type == 'monthly':
            day, time_str = time_value.split(' ')
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(execute_task, 'cron', day=day, hour=hour, minute=minute,
                              args=[name, device_name, command], id=job_id)
        elif task_type == 'yearly':
            month, day, time_str = time_value.split(' ')
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(execute_task, 'cron', month=month, day=day, hour=hour, minute=minute,
                              args=[name, device_name, command], id=job_id)
    except Exception as e:
        print(f"❌ 加载定时任务失败 [{name}]: {e}")


# --- 4. 异步生命周期管理（包含本地证书与安全登录逻辑） ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mqtt_client

    if MQTT_AVAILABLE:
        try:
            mqtt_client = mqtt.Client(
                client_id=MY_CLIENT_ID,
                clean_session=True
            )
            mqtt_client.on_connect = on_mqtt_connect

            # 【安全登录适配】：注入您提供的真实认证鉴权信息
            mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

            # 【证书核心适配】：动态寻找当前目录下的 emqxsl-ca.crt 证书文件
            base_dir = os.path.dirname(__file__)
            cert_path = os.path.join(base_dir, CA_CERT_NAME)

            if os.path.exists(cert_path):
                mqtt_client.tls_set(ca_certs=cert_path, tls_version=ssl.PROTOCOL_TLSv1_2)
                print(f"ℹ️ [TLS 安全层] 成功加载本地专属证书: {CA_CERT_NAME}")
            else:
                mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
                print(f"⚠️ [TLS 安全层] 未在当前目录找到 {CA_CERT_NAME}，已降级自动加载系统内置证书。")

            # 建立长连接并开启后台长连接心跳循环守护（Keep-Alive 60秒）
            mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
            mqtt_client.loop_start()
            print(f"ℹ️ MQTT 安全通道凭据配置完毕，正在向云端发起验证与握手...")
        except Exception as e:
            print(f"⚠️ 启动 MQTT 安全客户端失败: {e}，系统将退化至虚拟沙盒模式。")

    # 启动定时引擎
    scheduler.start()
    db = SessionLocal()
    tasks = db.query(TaskModel).all()
    for task in tasks:
        add_task_to_scheduler(task.id, task.name, task.task_type, task.time_value, task.device_name, task.command,
                              task.is_active)
    db.close()

    print("🚀 智能控制中心系统已成功启动...")
    yield

    # 平稳平退
    scheduler.shutdown()
    if MQTT_AVAILABLE and mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("💾 MQTT 安全连接通道已平稳关闭。")


app = FastAPI(title="智能设备核心集成系统", lifespan=lifespan)


class TaskCreate(BaseModel):
    name: str
    task_type: str
    time_value: str
    device_name: str
    command: str


class DeviceCreate(BaseModel):
    name: str
    uuid: str
    device_type: str
    location: str
    status: str
    data: str


class ImmediateControl(BaseModel):
    command: str


class CountdownControl(BaseModel):
    seconds: int
    command: str


# --- 5. API 路由接口 ---

@app.get("/api/devices")
def get_devices():
    db = SessionLocal()
    devices = db.query(DeviceModel).all()
    db.close()
    return devices


@app.get("/api/devices/{name}")
def get_device_by_name(name: str):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == name).first()
    db.close()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    return device


@app.post("/api/devices")
def create_device(device: DeviceCreate):
    db = SessionLocal()
    existing = db.query(DeviceModel).filter(
        (DeviceModel.name == device.name) | (DeviceModel.uuid == device.uuid)).first()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail="设备名称或 UUID 已存在")
    db_device = DeviceModel(name=device.name, uuid=device.uuid, device_type=device.device_type,
                            location=device.location, status=device.status, data=device.data)
    db.add(db_device)
    db.commit()
    db.close()
    return {"message": "设备添加成功"}


@app.post("/api/devices/{name}/control")
def immediate_control_device(name: str, payload: ImmediateControl):
    db = SessionLocal()
    device = db.query(DeviceModel).filter(DeviceModel.name == name).first()
    if not device:
        db.close()
        raise HTTPException(status_code=404, detail="设备不存在")

    if payload.command not in ["ON", "OFF"]:
        db.close()
        raise HTTPException(status_code=400, detail="开关器仅接受 ON 或 OFF 指令")

    # 【核心修复】：在关闭数据库和提交前，先把关键的 uuid 提取成普通的字符串变量
    device_uuid = device.uuid

    # 数据库保存最新下发的动作命令字符串
    device.data = payload.command
    db.commit()
    db.close()  # 👈 此时关闭连接，后面使用 device_uuid 变量就不会触发重新查询

    # 动态拼接主题名，将控制内容直接以外发
    dynamic_topic = f"SWITCH/{device_uuid}/POWER"
    sendCommandToMqtt(dynamic_topic, payload.command)

    return {"message": f"设备已即时切换为 {payload.command}", "current_data": payload.command}

@app.post("/api/devices/{name}/countdown")
def register_device_countdown(name: str, payload: CountdownControl):
    if payload.command not in ["ON", "OFF"]:
        raise HTTPException(status_code=400, detail="倒计时终点指令仅支持 ON 或 OFF")
    if payload.seconds <= 0:
        raise HTTPException(status_code=400, detail="倒计时秒数必须大于 0")

    target_time = datetime.now() + timedelta(seconds=payload.seconds)
    job_id = f"countdown_{name}_{int(datetime.now().timestamp())}"
    scheduler.add_job(execute_countdown_task, 'date', run_date=target_time, args=[name, payload.command], id=job_id)
    return {"message": f"成功注册倒计时任务，将在 {payload.seconds} 秒后执行"}


@app.get("/api/tasks")
def get_tasks(device_name: str = None):
    db = SessionLocal()
    if device_name:
        tasks = db.query(TaskModel).filter(TaskModel.device_name == device_name).all()
    else:
        tasks = db.query(TaskModel).all()
    db.close()
    return tasks


@app.post("/api/tasks")
def create_task(task: TaskCreate):
    db = SessionLocal()
    db_task = TaskModel(name=task.name, task_type=task.task_type, time_value=task.time_value,
                        device_name=task.device_name, command=task.command, is_active=True)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    add_task_to_scheduler(db_task.id, db_task.name, db_task.task_type, db_task.time_value, db_task.device_name,
                          db_task.command, db_task.is_active)
    db.close()
    return {"message": "任务创建成功"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    db = SessionLocal()
    db_task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if db_task:
        db.delete(db_task)
        db.commit()
    db.close()
    if scheduler.get_job(str(task_id)):
        scheduler.remove_job(str(task_id))
    return {"message": "任务删除成功"}


@app.put("/api/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    db = SessionLocal()
    db_task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if db_task:
        db_task.is_active = not db_task.is_active
        db.commit()
        add_task_to_scheduler(db_task.id, db_task.name, db_task.task_type, db_task.time_value, db_task.device_name,
                              db_task.command, db_task.is_active)
    db.close()
    return {"message": "状态切换成功"}


@app.get("/")
def read_index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/detail")
def read_detail():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "detail.html"))


@app.get("/query")
def read_query():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "query.html"))


@app.get("/favicon.ico", include_in_schema=False)
def get_favicon():
    # 动态寻找 static 目录下的真实图标并返回
    favicon_path = os.path.join(os.path.dirname(__file__), "static", "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204) # 如果文件不小心被删了，自动降级为 204


if __name__ == '__main__':
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)