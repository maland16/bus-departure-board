#ifndef SUPERVISOR_H
#define SUPERVISOR_H

#include <Arduino.h>
#include <FreeRTOS.h>
#include <task.h>


void registerTaskWithSupervisor(TaskHandle_t handle, const char *name, uint32_t stackDepth);
void vSupervisorTask(void *pvParameters);
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName);


#endif // SUPERVISOR_H
