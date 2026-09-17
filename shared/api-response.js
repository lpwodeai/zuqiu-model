/**
 * 统一API响应格式模块
 * 确保浏览器端和服务器端返回相同结构
 */

class ApiResponse {
  static success(data, message = '操作成功') {
    return {
      success: true,
      message,
      data,
      timestamp: new Date().toISOString()
    };
  }

  static error(message, code = 'UNKNOWN_ERROR', details = null) {
    return {
      success: false,
      message,
      error: {
        code,
        message,
        details
      },
      timestamp: new Date().toISOString()
    };
  }

  static validationError(message, code = 'VALIDATION_ERROR', field = null) {
    return {
      success: false,
      message,
      error: {
        code,
        message,
        field
      },
      timestamp: new Date().toISOString()
    };
  }

  static notFound(message = '资源未找到', code = 'NOT_FOUND') {
    return {
      success: false,
      message,
      error: {
        code,
        message
      },
      timestamp: new Date().toISOString()
    };
  }

  static unauthorized(message = '未授权访问', code = 'UNAUTHORIZED') {
    return {
      success: false,
      message,
      error: {
        code,
        message
      },
      timestamp: new Date().toISOString()
    };
  }

  static prediction(data) {
    return {
      success: true,
      message: '预测成功',
      data,
      timestamp: new Date().toISOString()
    };
  }

  static predictions(data, count = null) {
    const response = {
      success: true,
      message: '批量预测成功',
      data,
      timestamp: new Date().toISOString()
    };
    if (count !== null) {
      response.count = count;
    }
    return response;
  }

  static teams(data, count) {
    return {
      success: true,
      message: '获取球队列表成功',
      data,
      count,
      timestamp: new Date().toISOString()
    };
  }
}

export { ApiResponse };
export default ApiResponse;