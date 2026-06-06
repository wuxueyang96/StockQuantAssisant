import argparse
import logging
import os
import signal
import sys
from app import create_app
from app.models.database import db_manager


PID_FILE = os.path.join(os.path.expanduser('~'), '.stockquant', 'server.pid')


def write_pid():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def read_pid():
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def remove_pid():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def parse_args():
    parser = argparse.ArgumentParser(description='股票量化助手后端服务')
    sub = parser.add_subparsers(dest='command', required=True)

    p_start = sub.add_parser('start', help='启动服务')
    p_start.add_argument('--host', default='0.0.0.0', help='监听地址 (默认: 0.0.0.0)')
    p_start.add_argument('--port', type=int, default=5000, help='监听端口 (默认: 5000)')
    p_start.add_argument('--debug', action='store_true', help='开启调试模式')

    sub.add_parser('stop', help='停止服务')
    return parser.parse_args()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = create_app()


def cmd_start(args):
    write_pid()
    try:
        logger.info(f"启动股票量化助手服务... {args.host}:{args.port} debug={args.debug}")
        app.run(host=args.host, port=args.port, debug=args.debug)
    except KeyboardInterrupt:
        logger.info("正在关闭服务...")
    finally:
        db_manager.close_all()
        remove_pid()
        logger.info("服务已关闭")


def cmd_stop():
    pid = read_pid()
    if pid is None:
        logger.warning("未找到运行中的服务 (PID 文件不存在)")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info(f"已发送停止信号至 PID {pid}")
    except ProcessLookupError:
        logger.warning(f"PID {pid} 进程不存在，清理 PID 文件")
        remove_pid()
    except PermissionError:
        logger.error(f"无权限停止 PID {pid}")


def main():
    args = parse_args()
    if args.command == 'start':
        cmd_start(args)
    elif args.command == 'stop':
        cmd_stop()


if __name__ == '__main__':
    main()
